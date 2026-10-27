import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from axure_mcp import server
from axure_mcp.client import BridgeError
from axure_mcp.macos import MacAxure, SUPPORTED_TOOLS
from axure_mcp.ops import AxureError


class MacTests(unittest.TestCase):
    def test_adapter_selected_from_bridge_runtime(self):
        bridge=Mock(); bridge.connect.return_value=bridge
        bridge.ping.return_value={'entry':'Client.Mac','appVersion':'11.0.0.4137'}
        with patch.object(server,'Bridge',return_value=bridge):
            app=server._connect_app()
        self.assertIsInstance(app,MacAxure)
        self.assertIs(app.b,bridge)

    def test_unknown_version_is_closed_before_model_access(self):
        bridge=Mock(); bridge.connect.return_value=bridge
        bridge.ping.return_value={'entry':'Client.Mac','appVersion':'11.0.0.9999'}
        with patch.object(server,'Bridge',return_value=bridge), self.assertRaises(AxureError):
            server._connect_app()
        bridge.close.assert_called_once()
        bridge.docs.assert_not_called()

    def test_unsupported_widget_rejected_before_reflection(self):
        bridge=Mock();app=MacAxure(bridge=bridge)
        for kind,text in [('connector',None),('textlink',None),('hotspot','bad')]:
            with self.assertRaises(AxureError):app.add_widget(1,kind,text=text)
        self.assertEqual(bridge.mock_calls,[])

    def test_delete_parent_containing_all_pages_is_rejected(self):
        app=MacAxure(bridge=Mock())
        app.pages=lambda:[{'id':'parent'},{'id':'child'}]
        app.page_tree=lambda:[{'id':'parent','children':[{'id':'child','children':[]}]}]
        with self.assertRaises(AxureError):app.delete_page('parent')
        with self.assertRaises(AxureError):app.move_page('parent','child')
        self.assertEqual(app.b.mock_calls,[])

    def test_active_document_is_not_the_first_open_document(self):
        bridge=Mock()
        bridge.docs.return_value=[{'handle':1,'docHandle':2,'isActive':False},
                                  {'handle':3,'docHandle':4,'isActive':True}]
        app=MacAxure(bridge=bridge)
        self.assertEqual(app.client,3)
        bridge.docs.return_value.reverse()
        self.assertEqual(app.document,4)
        bridge.docs.return_value=[{'handle':1},{'handle':3}]
        with self.assertRaises(AxureError): _=app.client

    def test_key_window_wins_over_stale_active_document(self):
        bridge=Mock()
        bridge.docs.return_value=[
            {'id':1,'handle':10,'docHandle':11,'isActive':True,'isKeyWindow':False},
            {'id':2,'handle':20,'docHandle':21,'isActive':False,'isKeyWindow':True},
        ]
        app=MacAxure(bridge=bridge)
        self.assertEqual(app.client,20)
        self.assertEqual(app.document,21)
        self.assertEqual(app._selected_document,2)

    def test_selected_document_survives_inactive_cocoa_window(self):
        bridge=Mock()
        docs=[{'id':1,'handle':10,'fileName':'/tmp/a.rp','isActive':False},
              {'id':2,'handle':20,'fileName':'/tmp/b.rp','isActive':False}]
        bridge.docs.return_value=docs
        app=MacAxure(bridge=bridge)
        with self.assertRaises(AxureError): _=app.client
        app._selected_document=2
        self.assertEqual(app.client,20)
        docs[0]['isActive']=True
        self.assertEqual(app.client,10)
        self.assertEqual(app._selected_document,1)

    def test_open_file_uses_cocoa_controller_and_selects_new_window(self):
        bridge=Mock();bridge.invoke.return_value={'$h':30}
        app=MacAxure(bridge=bridge)
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'new.rp';target.write_bytes(b'rp')
            realpath=str(target.resolve())
            bridge.docs.return_value=[
                {'id':1,'handle':10,'fileName':str(Path(tmp)/'old.rp'),'isActive':False},
                {'id':2,'handle':20,'fileName':realpath,'isActive':False},
            ]
            self.assertEqual(app.open_file(str(target)),realpath)
            bridge.invoke.assert_called_once_with(
                'MCocoa.NSDocumentController','sharedDocumentController')
            bridge.invoke_on.assert_called_once_with(30,'OpenDocument',[realpath,False])
            app._client=None
            self.assertEqual(app.client,20)
            self.assertEqual(app._selected_document,2)

    def test_open_file_waits_for_registration_and_prefers_key_window(self):
        bridge=Mock();bridge.invoke.return_value={'$h':30}
        app=MacAxure(bridge=bridge)
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'new.rp';target.write_bytes(b'rp')
            realpath=str(target.resolve())
            documents=[
                {'id':1,'handle':10,'fileName':str(Path(tmp)/'old.rp')},
                {'id':2,'handle':20,'fileName':realpath,'isActive':True,
                 'isKeyWindow':False},
                {'id':3,'handle':30,'fileName':realpath,'isActive':False,
                 'isKeyWindow':True},
            ]
            bridge.docs.side_effect=[
                documents[:1],
                documents,
                documents,
            ]
            with patch.object(app,'OPEN_DOCUMENT_TIMEOUT',1), \
                    patch('axure_mcp.macos.time.sleep'):
                self.assertEqual(app.open_file(str(target)),realpath)
            app._client=None
            self.assertEqual(app.client,30)
            self.assertEqual(app._selected_document,3)

    def test_open_file_rejects_missing_file_before_connecting(self):
        bridge=Mock();app=MacAxure(bridge=bridge)
        with self.assertRaises(AxureError):
            app.open_file('/tmp/does-not-exist.rp')
        self.assertEqual(bridge.mock_calls,[])

    def test_open_file_requires_a_matching_document_window(self):
        bridge=Mock();bridge.invoke.return_value={'$h':30}
        bridge.docs.return_value=[{'id':1,'handle':10,'fileName':'/tmp/old.rp'}]
        app=MacAxure(bridge=bridge)
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'new.rp';target.write_bytes(b'rp')
            with patch.object(app,'OPEN_DOCUMENT_TIMEOUT',0), \
                    self.assertRaises(AxureError):
                app.open_file(str(target))

    def test_old_handle_rebinds_by_guid_only_within_requested_page(self):
        bridge=Mock(); app=MacAxure(bridge=bridge)
        app.objects=lambda package: 100
        app._collect_handles=lambda objects,deep=False:[20,21]
        ids={10:'persistent-id',20:'other-id',21:'persistent-id',30:'foreign-id'}
        bridge.invoke_on.side_effect=lambda handle,method: ids[handle]
        self.assertEqual(app.resolve_widget(1,handle=10),21)
        with self.assertRaises(AxureError):app.resolve_widget(1,handle=30)

    def test_invalid_content_edits_do_not_mutate(self):
        bridge=Mock(); app=MacAxure(bridge=bridge)
        with self.assertRaises(AxureError):app.set_list_items(1,items=['a'],selected=1)
        with self.assertRaises(AxureError):app.add_panel_state(1,count=0)
        with self.assertRaises(AxureError):app._top_widgets(1,['a','a'])
        with self.assertRaises(AxureError):app._mac_action(1,{'action':'wait','typo':1})
        self.assertEqual(bridge.mock_calls,[])

    def test_table_preflight_rejects_invalid_dimensions_without_writes(self):
        bridge=Mock(); app=MacAxure(bridge=bridge)
        app.resolve_widget=lambda *args:1
        bridge.identity.return_value={'type':'Axure.DocumentModel.Objects.Table'}
        for options in ({'rows':0,'columns':2},
                        {'rows':2,'columns':2,'row_heights':[20]},
                        {'rows':1,'columns':1,'data':[['a','b']]},
                        {'rows':1,'columns':1,'data':[['a',2]]},
                        {'rows':1,'columns':1,'data':['a']},
                        {'rows':True,'columns':1},
                        {'rows':1,'columns':1,'row_heights':[True]},
                        {'rows':200,'columns':200}):
            with self.subTest(options=options), self.assertRaises(AxureError):
                app.set_table(1,**options)
        bridge.set_on.assert_not_called()
        bridge.invoke_on.assert_not_called()

    def test_table_resizes_before_resolving_the_rebuilt_cell_collection(self):
        bridge=Mock();app=MacAxure(bridge=bridge)
        app.resolve_widget=lambda *args:1
        app.widget_info=lambda widget:{'Name':'grid'}
        bridge.identity.return_value={'type':'Axure.DocumentModel.Objects.Table'}
        bridge.get_on.side_effect=lambda handle,name: {
            'Rows':1,'Columns':1,'AllTableCells':{'$h':10}
        }[name]
        bridge.iter.return_value=[{'$h':20},{'$h':21},{'$h':22},{'$h':23}]
        app.set_table(1,rows=2,columns=2,data=[['a','b'],['c']])
        self.assertEqual(bridge.set_on.call_args_list[:2], [
            call(1,'Rows',2),
            call(1,'Columns',2),
        ])
        self.assertEqual(bridge.set_on.call_args_list[2:], [
            call(20,'Text','a'),
            call(21,'Text','b'),
            call(22,'Text','c'),
        ])

    def test_table_resize_rolls_back_if_native_cells_remain_incomplete(self):
        bridge=Mock();app=MacAxure(bridge=bridge)
        app.resolve_widget=lambda *args:1
        bridge.identity.return_value={'type':'Axure.DocumentModel.Objects.Table'}
        bridge.get_on.side_effect=lambda handle,name: {
            'Rows':1,'Columns':1,'AllTableCells':{'$h':10}
        }[name]
        bridge.iter.return_value=[{'$h':20}]
        with self.assertRaises(AxureError):
            app.set_table(1,rows=2,columns=2)
        self.assertEqual(bridge.set_on.call_args_list, [
            call(1,'Rows',2),
            call(1,'Columns',2),
            call(1,'Rows',1),
            call(1,'Columns',1),
        ])

    def test_note_writer_clones_shared_default_before_editing(self):
        bridge=Mock();app=MacAxure(bridge=bridge)
        bridge.invoke_on.side_effect=[{'$h':10},{'$h':11},True]
        result=app._write_note(1,[{'name':'Note','id':'guid'}],'independent',None)
        bridge.set_on.assert_called_once_with(11,'Text','independent')
        self.assertEqual(bridge.invoke_on.call_args_list[1].args,(10,'tAs4'))
        self.assertEqual(result['text'],'independent')

    def test_empty_image_readback_tolerates_missing_backing_stream(self):
        bridge=Mock();app=MacAxure(bridge=bridge)
        bridge.get_on.side_effect=[{'$h':10},BridgeError('NullReferenceException')]
        self.assertEqual(app.image_bytes(1),b'')
        bridge.get_on.side_effect=[{'$h':10},BridgeError('bridge closed')]
        with self.assertRaises(BridgeError):
            app.image_bytes(1)

    def test_page_read_degrades_when_one_content_readback_fails(self):
        app=Mock()
        app.widgets.return_value=[{'handle':10,'Name':'image'}]
        app.widget_content.side_effect=BridgeError('missing stream')
        with patch.object(server,'_app',return_value=app), \
                patch.object(server,'_open',return_value=(1,'page-id','Page')):
            result=json.loads(server.tool_axure_get_page({})['text'])
        widget=result['widgets'][0]
        self.assertEqual(widget['Name'],'image')
        self.assertEqual(widget['content_error'],'missing stream')

    def test_invalid_image_base64_is_rejected_before_connecting(self):
        with patch.object(server,'_app') as get_app, self.assertRaises(AxureError):
            server.tool_axure_set_image({'image_base64':'not base64!'})
        get_app.assert_not_called()

    def test_collect_handles_tolerates_ancestry_failures_and_deduplicates(self):
        bridge=Mock();app=MacAxure(bridge=bridge)
        bridge.iter.return_value=[{'$h':10},{'$h':10},{'$h':11},{'$h':12}]
        def invoke(handle,method):
            if method=='Cb6A':
                if handle==10:return 'group-guid'
                if handle==11:raise BridgeError('no ancestry property')
                return None
            return {10:'hidden',11:'widget-11',12:'widget-12'}[handle]
        bridge.invoke_on.side_effect=invoke
        self.assertEqual(app._collect_handles(99),[11,12])

    def test_group_insert_failure_rolls_back_without_duplicating_members(self):
        bridge=Mock();app=MacAxure(bridge=bridge)
        app._top_widgets=lambda package,names:[10,11]
        app.find_widget=lambda package,name:None
        app.add_widget=lambda *args,**kwargs:20
        app.objects=lambda package:30
        calls=[]
        def invoke(handle,method,args):
            calls.append((handle,method,args))
            if handle==20 and method=='Add' and args[0]['$h']==11:
                raise BridgeError('membership failed')
            return True
        bridge.invoke_on.side_effect=invoke
        with self.assertRaises(BridgeError):
            app.group(1,['a','b'],'group')
        self.assertIn((20,'Remove',[{'$h':10}]),calls)
        self.assertIn((30,'Remove',[{'$h':20}]),calls)
        self.assertFalse(any(handle==30 and method=='Add' for handle,method,_ in calls))

    def test_mac_nested_actions_require_correct_types(self):
        bridge=Mock();app=MacAxure(bridge=bridge)
        app.resolve_widget=lambda *args:10
        for spec in ([], {'action':{}}, {'action':'wait','typo':None},
                     {'action':'move','target_widget':'a','value':10,'mode':1}):
            with self.subTest(spec=spec), self.assertRaises(AxureError):
                app._mac_action(1,spec)
        self.assertEqual(bridge.mock_calls,[])

    def test_mac_public_schema_limits_order_and_batch_size(self):
        with patch.object(server.sys,'platform','darwin'):
            with self.assertRaises(AxureError):
                server._validate_args('axure_z_order',{'names':['a'],'action':'forward'})
            too_many=[{'action':'wait','wait_ms':0} for _ in range(101)]
            with self.assertRaises(AxureError):
                server._validate_args(
                    'axure_add_interaction',{'event':'OnClick','actions':too_many})
            with self.assertRaises(AxureError):
                server._validate_args(
                    'axure_add_interaction',
                    {'event':'OnClick','actions':[{'action':'wait','unknown':None}]})

    def test_capabilities_are_a_subset_of_registered_tools(self):
        self.assertEqual(len(SUPPORTED_TOOLS),35)
        self.assertTrue(SUPPORTED_TOOLS <= server.HANDLERS.keys())


if __name__=='__main__':unittest.main()
