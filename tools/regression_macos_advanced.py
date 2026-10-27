"""Exercise advanced Mac capabilities via actual stdio MCP, then verify after restart.

Only edits the exact disposable file specified by --exercise; never stops Axure.
Use --verify REPORT after quitting/relaunching the hooked app with the same file.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct
import zlib

from regression_macos import MCP, require_document, stable


def png(colour):
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',8,8,8,6,0,0,0))+
            chunk(b'IDAT',zlib.compress((b'\0'+bytes(colour)*8)*8))+chunk(b'IEND',b''))


def snapshot(m, pid):
    page=m.call('axure_get_page',page_id=pid,deep=True)
    return {'widgets':stable(page),'order':[w['Name'] for w in page['widgets']],
            'page_notes':m.call('axure_get_page_notes',page_id=pid),
            'widget_notes':m.call('axure_get_widget_notes',page_id=pid),
            'interactions':m.call('axure_get_interactions',page_id=pid,name='actions')}


def exercise(m, path, report):
    path=Path(path);report=Path(report)
    if not report.parent.is_dir():raise RuntimeError('report directory does not exist')
    if report.exists():raise RuntimeError('choose a new report path')
    status=require_document(m,path)
    advertised=m.rpc('tools/list',{})['tools']
    assert len(advertised)==35
    folder=m.call('axure_create_folder',name='Mac advanced regression')['folder']
    page=m.call('axure_create_page',name='Advanced persistence',parent_id=folder['id'])['page']
    pid=page['id']
    dest=m.call('axure_create_page',name='Navigation destination',parent_id=folder['id'])['page']
    handles={}
    kinds={'list':'droplist','listbox':'listbox','picture':'image','grid':'table','panel':'dynamicpanel',
           'rich':'shape','plain':'textarea','a':'button','b':'button','c':'button','d':'button',
           'actions':'button','target':'shape'}
    for i,(name,kind) in enumerate(kinds.items()):
        handles[name]=m.call('axure_add_widget',page_id=pid,kind=kind,name=name,
                            x=30+i%4*220,y=30+i//4*140,width=180,height=90)['widget']['handle']
    m.call('axure_set_list_items',page_id=pid,name='list',items=['北京','上海','广州'],selected=1)
    m.call('axure_set_list_items',page_id=pid,name='listbox',items=['初始','完成'],selected=0)
    m.call('axure_set_list_items',page_id=pid,name='listbox',items=[])
    first,second=png([255,32,64,255]),png([32,255,64,255])
    assert len(first)==len(second) and first!=second
    for raw in (first,second):
        result=m.call('axure_set_image',page_id=pid,widget_id=handles['picture'],
                      image_base64=base64.b64encode(raw).decode(),filename='replacement.png')
        assert result['sha256']==hashlib.sha256(raw).hexdigest()
    m.call('axure_set_table',page_id=pid,name='grid',rows=2,columns=3,
           data=[['姓名','城市','状态'],['Miao','上海','正常']],
           row_heights=[30,40],column_widths=[100,110,120])
    m.call('axure_set_rich_text',page_id=pid,name='rich',paragraphs=['第一段','Second paragraph'])
    m.call('axure_set_rich_text',page_id=pid,name='plain',paragraphs=['Plain 第一行','第二行'])
    initial=m.call('axure_panel_states',page_id=pid,name='panel')
    added=m.call('axure_add_panel_state',page_id=pid,name='panel',count=2,state_name='已完成')
    assert added['count']==initial['count']+2 and added['states'][-1]=='已完成'
    m.call('axure_set_page_note',page_id=pid,text='页面备注独立保留')
    for name,text in [('a','按钮 A 备注'),('b','按钮 B 备注')]:
        m.call('axure_set_widget_note',page_id=pid,name=name,text=text)
    m.call('axure_group',page_id=pid,names=['a','b'],group_name='persistent_group')
    m.call('axure_update_widget',page_id=pid,widget_id=handles['a'],text='group member retained')
    m.call('axure_group',page_id=pid,names=['c','d'],group_name='temporary_group')
    m.call('axure_ungroup',page_id=pid,name='temporary_group')
    m.call('axure_z_order',page_id=pid,names=['plain'],action='front')
    page_after=m.call('axure_get_page',page_id=pid)
    assert page_after['widgets'][-1]['Name']=='plain'
    m.call('axure_z_order',page_id=pid,names=['grid'],action='back')
    assert m.call('axure_get_page',page_id=pid)['widgets'][0]['Name']=='grid'
    actions=[{'action':'wait','wait_ms':0},{'action':'set_opacity','target_widget':'target','value':0.4},
             {'action':'move','target_widget':'target','value':50,'mode':'By'},
             {'action':'rotate','target_widget':'target','value':45},
             {'action':'resize','target_widget':'target','value':1.5},
             {'action':'navigate','target_page_id':dest['id']},{'action':'close'}]
    m.call('axure_add_interaction',page_id=pid,name='actions',event='OnClick',actions=actions)
    m.call('axure_add_interaction',page_id=pid,name='actions',event='OnClick',action='wait',wait_ms=125)
    expected=snapshot(m,pid)
    assert len(expected['interactions']['interactions'][0]['cases'])==2
    assert len(expected['interactions']['interactions'][0]['cases'][0]['actions'])==7
    text=json.dumps(expected['interactions'],ensure_ascii=False)
    assert 'target 40%' in text and 'Navigation destination' in text
    widgets={w['Name']:w for w in expected['widgets']}
    assert widgets['picture']['content']['sha256']==hashlib.sha256(second).hexdigest()
    assert widgets['grid']['content']['column_widths']==[100,110,120]
    assert widgets['grid']['content']['row_heights']==[30,40]
    assert 'a' in widgets and 'b' in widgets and 'persistent_group' in widgets
    assert 'temporary_group' not in widgets and 'c' in widgets and 'd' in widgets
    assert '页面备注独立保留' in json.dumps(expected['page_notes'],ensure_ascii=False)
    note_text=json.dumps(expected['widget_notes'],ensure_ascii=False)
    assert '按钮 A 备注' in note_text and '按钮 B 备注' in note_text
    for tool,args in [
        ('axure_set_list_items',{'name':'list','items':['a'],'selected':5}),
        ('axure_set_table',{'name':'grid','rows':0}),
        ('axure_set_table',{'name':'grid','rows':2,'row_heights':[10]}),
        ('axure_group',{'names':['a','a'],'group_name':'bad'}),
        ('axure_move_page',{'page_id':folder['id'],'parent_id':pid}),
        ('axure_add_interaction',{'name':'actions','event':'OnClick','actions':[{'action':'wait'}, {'action':'unknown'}]}),
    ]:
        rejected=m.call(tool,expect_error=True,**({'page_id':pid,**args} if tool!='axure_move_page' else args))
        assert 'AxureError' in rejected
        assert snapshot(m,pid)==expected,'rejected call modified document'
    # Nested folder move, rename, delete with a protected remaining page.
    nested=m.call('axure_create_folder',name='temporary folder',parent_id=folder['id'])['folder']
    temp=m.call('axure_create_page',name='temporary page',parent_id=nested['id'])['page']
    m.call('axure_move_page',page_id=nested['id'],index=0)
    m.call('axure_rename_page',page_id=nested['id'],name='temporary renamed')
    m.call('axure_delete_page',page_id=nested['id'])
    # Moving a folder must not make it impossible to read or persist the sitemap.
    m.call('axure_move_page',page_id=folder['id'],index=0)
    expected=snapshot(m,pid)
    m.call('axure_save')
    data={'file':str(path),'original_pid':status['runtime']['pid'],'runtime':status['runtime'],
          'page_id':pid,'folder_id':folder['id'],'target_page_id':dest['id'],
          'advertised_tools':len(advertised),'expected':expected,'calls':m.calls}
    report.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(report),'phase':'exercise','passed':True,
                      'advertised_tools':len(advertised),'calls':len(m.calls)},ensure_ascii=False))


def verify(m,report):
    data=json.loads(report.read_text(encoding='utf-8'))
    status=require_document(m,data['file'])
    assert status['runtime']['pid']!=data['original_pid'],'restart Axure before verification'
    assert snapshot(m,data['page_id'])==data['expected'],'saved advanced state differs after restart'
    tree=m.call('axure_page_tree')['tree']
    folder=next(n for n in tree if n['id']==data['folder_id'])
    assert {p['id'] for p in folder['children']}=={data['page_id'],data['target_page_id']}
    data['restart_verification']={'pid':status['runtime']['pid'],'advanced_state_matches':True,
                                  'nested_sitemap_matches':True}
    report.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(data['restart_verification']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--exercise',type=Path);mode.add_argument('--verify',type=Path)
    p.add_argument('--report',type=Path)
    args=p.parse_args();m=MCP()
    try:
        if args.exercise:
            if not args.report or not args.report.is_absolute() or not args.exercise.is_absolute():
                p.error('absolute --exercise and --report required')
            exercise(m,args.exercise,args.report)
        else:
            if not args.verify.is_absolute():p.error('absolute --verify required')
            verify(m,args.verify)
    finally:m.close()


if __name__=='__main__':main()
