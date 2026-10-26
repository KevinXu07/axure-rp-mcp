"""Live stdio MCP regression against an explicitly selected disposable Mac document.

Open a COPY of test/clean.rp with the injected Mac bridge first. This script refuses
to edit any other document. After exercise, exit/relaunch Axure with that file and
run --verify against the report. No Axure process is killed by this script.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from axure_mcp.macos import SUPPORTED_TOOLS, SUPPORTED_WIDGETS, TEXT_WIDGETS
from axure_mcp.server import TOOLS


class MCP:
    def __init__(self):
        self.proc = subprocess.Popen([sys.executable, '-m', 'axure_mcp.server'], cwd=REPO,
            env={**os.environ, 'AXURE_AUTO_LAUNCH':'0', 'PYTHONIOENCODING':'utf-8'},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        self.ident = 0
        self.calls = []
        self.rpc('initialize', {'protocolVersion':'2024-11-05','capabilities':{},
                               'clientInfo':{'name':'mac-regression','version':'1'}})
    def rpc(self, method, params):
        self.ident += 1
        self.proc.stdin.write(json.dumps({'jsonrpc':'2.0','id':self.ident,
                                         'method':method,'params':params})+'\n')
        self.proc.stdin.flush()
        # Keep every live call bounded, including a hung UI thread.
        import queue
        import threading
        replies = queue.Queue()
        threading.Thread(target=lambda: replies.put(self.proc.stdout.readline()), daemon=True).start()
        try: line = replies.get(timeout=30)
        except queue.Empty: raise RuntimeError(f'MCP timeout: {method} {params}')
        reply = json.loads(line)
        if 'error' in reply: raise RuntimeError(reply['error'])
        return reply['result']
    def call(self, tool_name, expect_error=False, **args):
        result = self.rpc('tools/call',{'name':tool_name,'arguments':args})
        self.calls.append({'name':tool_name,'isError':bool(result.get('isError'))})
        if bool(result.get('isError')) != expect_error:
            raise AssertionError((tool_name,args,result))
        text = result['content'][0]['text']
        if expect_error: return text
        return json.loads(text)
    def close(self):
        self.proc.stdin.close()
        try: self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill(); self.proc.wait()


def require_document(mcp, path):
    status = mcp.call('axure_status')
    actual = status['document']['file']
    if Path(actual).resolve() != Path(path).resolve():
        raise RuntimeError(f'Refusing to edit current document: expected {path}, got {actual}')
    return status


def exercise(mcp, path, report):
    if Path(report).exists(): raise RuntimeError('report already exists; choose a new report path')
    status = require_document(mcp,path)
    advertised = mcp.rpc('tools/list',{})['tools']
    assert {t['name'] for t in advertised} == SUPPORTED_TOOLS
    page = mcp.call('axure_create_page',name='MCP Mac 验证')['page']
    pid = page['id']
    doomed = mcp.call('axure_create_page',name='MCP temporary')['page']
    mcp.call('axure_rename_page',page_id=doomed['id'],name='MCP renamed temporary')
    mcp.call('axure_move_page',page_id=doomed['id'],index=1)
    mcp.call('axure_delete_page',page_id=doomed['id'])
    for i, kind in enumerate(sorted(SUPPORTED_WIDGETS)):
        args = dict(kind=kind,name='mcp_'+kind,page_id=pid,
                    x=30+(i%4)*180,y=30+(i//4)*100,width=155,height=55)
        if kind in TEXT_WIDGETS: args['text']='Mac 测试 '+kind
        mcp.call('axure_add_widget',**args)
    mcp.call('axure_update_widget',page_id=pid,name='mcp_button',
             text='保存并重开后仍然存在 ✓',x=40,y=40,width=210,height=48)
    mcp.call('axure_style_widget',page_id=pid,name='mcp_button',
             fill='#123456',text_color='#FFFFFF',border_color='#ABCDEF',
             border_width=2,corner_radius=8,opacity=0.8,font='Arial',font_size=18,
             bold=True,italic=True,underline=True,align='center',valign='middle',
             padding=[8,9,10,11],rotation=3,line_spacing=1.5,letter_spacing=2)
    unnamed = mcp.call('axure_add_widget',page_id=pid,kind='button')['widget']
    listed = mcp.call('axure_get_page',page_id=pid)['widgets']
    assert any(w.get('Name') == '' for w in listed), 'unnamed widgets must remain visible to MCP'
    mcp.call('axure_delete_widget',page_id=pid,widget_id=unnamed['handle'])
    mcp.call('axure_add_widget',page_id=pid,kind='button',name='mcp_deleted')
    mcp.call('axure_delete_widget',page_id=pid,name='mcp_deleted')
    layout = ['mcp_textbox','mcp_textarea','mcp_shape']
    mcp.call('axure_align',page_id=pid,names=layout,mode='left')
    mcp.call('axure_same_size',page_id=pid,names=layout,mode='both')
    mcp.call('axure_distribute',page_id=pid,names=layout,axis='vertical',gap=18)
    expected_page = mcp.call('axure_get_page',page_id=pid)
    style = mcp.call('axure_get_widget_style',page_id=pid,name='mcp_button')['style']
    mcp.call('axure_page_tree')
    mcp.call('axure_list_pages')
    unsupported=[]
    for tool in TOOLS:
        if tool['name'] not in SUPPORTED_TOOLS:
            # Generate syntactically valid required arguments. The capability
            # guard must fire before any reflection access or mutation.
            props=tool['inputSchema']['properties']
            args={}
            for key in tool['inputSchema'].get('required',[]):
                spec=props[key]
                args[key]=(spec.get('enum') or ['unsupported'])[0] if spec.get('type')=='string' else ['unsupported'] if spec.get('minItems') else []
            text=mcp.call(tool['name'],expect_error=True,**args)
            assert 'not supported by the Mac' in text, text
            unsupported.append(tool['name'])
    after=mcp.call('axure_get_page',page_id=pid)
    assert stable(after)==stable(expected_page)
    mcp.call('axure_save')
    copy_path=Path(path).with_name(Path(path).stem+'-saved-copy.rp')
    if copy_path.exists(): raise RuntimeError(f'copy already exists: {copy_path}')
    mcp.call('axure_save_as',path=str(copy_path))
    assert copy_path.read_bytes()==Path(path).read_bytes()
    # Reopen the exact same document using the MCP tool, and verify fresh handles.
    mcp.call('axure_open_file',path=str(path))
    require_document(mcp,path)
    assert stable(mcp.call('axure_get_page',page_id=pid))==stable(expected_page)
    data={'file':str(path),'runtime':status['runtime'],'page_id':pid,
          'widgets':stable(expected_page),'style':style,'unsupported':unsupported,
          'calls':mcp.calls,'copy':str(copy_path),'original_pid':status['runtime']['pid']}
    Path(report).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'phase':'exercise','tools':len(set(c['name'] for c in mcp.calls if not c['isError'])),
                      'widgets':len(data['widgets']),'unsupported_gated':len(unsupported),'report':str(report)},ensure_ascii=False))


def stable(page):
    return sorted([{k:v for k,v in w.items() if k!='handle'} for w in page['widgets']],key=lambda w:w['Name'])


def verify(mcp, report):
    data=json.loads(Path(report).read_text(encoding='utf-8'))
    status=require_document(mcp,data['file'])
    assert status['runtime']['pid']!=data['original_pid'],'Exit and restart Axure before --verify'
    actual=mcp.call('axure_get_page',page_id=data['page_id'])
    assert stable(actual)==data['widgets'],(stable(actual),data['widgets'])
    style=mcp.call('axure_get_widget_style',page_id=data['page_id'],name='mcp_button')['style']
    assert style==data['style'],(style,data['style'])
    pages=mcp.call('axure_list_pages')['pages']
    assert not any('temporary' in p['name'] for p in pages)
    data['restart_verification']={'pid':status['runtime']['pid'],'widgets_match':True,
                                  'style_matches':True,'temporary_page_absent':True}
    Path(report).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(data['restart_verification']))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--exercise',type=Path,help='Absolute disposable .rp path already open in Axure')
    mode.add_argument('--verify',type=Path,help='Exercise report, after a real Axure process restart')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    mcp=MCP()
    try:
        if args.exercise:
            if not args.exercise.is_absolute() or not args.report: parser.error('absolute --exercise and --report required')
            exercise(mcp,args.exercise,args.report)
        else: verify(mcp,args.verify)
    finally: mcp.close()


if __name__=='__main__': main()
