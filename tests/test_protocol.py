import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from axure_mcp import server
from axure_mcp.ops import AxureError


class ProtocolTests(unittest.TestCase):
    def request(self, payload):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            server.handle_request(payload)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_malformed_requests_do_not_raise(self):
        for payload in (None, 1, [], 'bad', {}, {'jsonrpc': '2.0', 'method': 5},
                        {'jsonrpc': '2.0', 'id': True, 'method': 'ping'}):
            with self.subTest(payload=payload):
                self.assertEqual(self.request(payload)[0]['error']['code'], -32600)

    def test_notifications_never_reply(self):
        for method in ('ping', 'tools/list', 'notifications/initialized', 'unknown'):
            self.assertEqual(self.request({'jsonrpc': '2.0', 'method': method}), [])

    def test_invalid_tool_envelopes(self):
        for params in ([], 'bad', 4, None):
            r = self.request({'jsonrpc':'2.0','id':1,'method':'tools/call','params':params})
            self.assertEqual(r[0]['error']['code'], -32602)
        for name in ([], {}, None):
            r = self.request({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name}})
            self.assertEqual(r[0]['error']['code'], -32602)

    def test_invalid_arguments_rejected_before_connecting(self):
        cases = [
            ('axure_create_page', {'name': None}),
            ('axure_create_page', []),
            ('axure_align', {'names': [1], 'mode': 'left'}),
            ('axure_add_widget', {'kind': 'button', 'width': float('nan')}),
            ('axure_add_widget', {'kind': 'button', 'height': float('inf')}),
            ('axure_add_widget', {'kind': 'button', 'height': -1}),
            ('axure_render_page', {'max_dimension': 0}),
            ('axure_style_widget', {'padding': ['bad']}),
        ]
        with patch.object(server, '_app', side_effect=AssertionError('must not connect')):
            for name, args in cases:
                with self.subTest(name=name, args=args):
                    r = self.request({'jsonrpc':'2.0','id':1,'method':'tools/call',
                                      'params':{'name':name,'arguments':args}})
                    self.assertTrue(r[0]['result']['isError'])
                    self.assertIn('AxureError',r[0]['result']['content'][0]['text'])

    def test_all_tool_schemas_reject_null_and_unknown_fields(self):
        for tool in server.TOOLS:
            with self.subTest(tool=tool['name']):
                with self.assertRaises(AxureError):
                    server._validate_args(tool['name'], None)
                with self.assertRaises(AxureError):
                    server._validate_args(tool['name'], {'unexpected': True})

    def test_stdio_is_utf8_even_with_legacy_environment(self):
        request={'jsonrpc':'2.0','id':1,'method':'tools/call',
                 'params':{'name':'不存在🧪'}}
        run=subprocess.run([sys.executable,'-m','axure_mcp.server'],
                           input=(json.dumps(request,ensure_ascii=False)+'\n').encode('utf-8'),
                           capture_output=True,timeout=5,
                           env={**os.environ,'PYTHONIOENCODING':'ascii'})
        self.assertEqual(run.returncode,0,run.stderr)
        reply=json.loads(run.stdout.decode('utf-8'))
        self.assertIn('不存在🧪',reply['error']['message'])

    def test_stdio_survives_bad_json_then_ping(self):
        messages = '\n'.join(['{', 'null', '[]', '42',
                              '{"jsonrpc":"2.0","method":"tools/list"}',
                              '{"jsonrpc":"2.0","id":9,"method":"ping"}'])+'\n'
        run = subprocess.run([sys.executable,'-m','axure_mcp.server'], input=messages,
                             capture_output=True,text=True,timeout=5,
                             env={**os.environ,'AXURE_AUTO_LAUNCH':'1'})
        self.assertEqual(run.returncode,0,run.stderr)
        replies = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(len(replies),5)
        self.assertEqual(replies[-1],{'jsonrpc':'2.0','id':9,'result':{}})


if __name__ == '__main__':
    unittest.main()
