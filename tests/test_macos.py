import unittest
from unittest.mock import Mock, patch

from axure_mcp import server
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

    def test_capabilities_are_a_subset_of_registered_tools(self):
        self.assertEqual(len(SUPPORTED_TOOLS),19)
        self.assertTrue(SUPPORTED_TOOLS <= server.HANDLERS.keys())


if __name__=='__main__':unittest.main()
