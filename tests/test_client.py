import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axure_mcp.client import Bridge, BridgeError, discover


class FakeSocket:
    def __init__(self, reply):
        self.reply = reply
        self.closed = False
    def sendall(self, data): pass
    def recv(self, size):
        if isinstance(self.reply, Exception): raise self.reply
        result, self.reply = self.reply, b''
        return result
    def close(self): self.closed = True


class ClientTests(unittest.TestCase):
    def test_dismiss_timeout_does_not_recursively_dismiss(self):
        b = Bridge(41999)
        s = b._sock = FakeSocket(socket.timeout('test'))
        with patch.object(b,'dismiss_dialogs',side_effect=AssertionError('recursive recovery')):
            with self.assertRaises(BridgeError): b.call('dismiss')
        self.assertTrue(s.closed)

    def test_timeout_discards_socket_and_buffer(self):
        b = Bridge(41999)
        s = b._sock = FakeSocket(socket.timeout('test'))
        b._buf = b'partial response'
        with patch.object(b,'dismiss_dialogs',return_value=[]):
            with self.assertRaises(BridgeError): b.call('get')
        self.assertIsNone(b._sock)
        self.assertEqual(b._buf,b'')
        self.assertTrue(s.closed)

    def test_mismatched_response_rejected(self):
        b = Bridge(41999)
        b._sock = FakeSocket(b'{"ok":true,"id":999,"result":"wrong"}\n')
        with self.assertRaises(BridgeError): b.call('ping')
        self.assertIsNone(b._sock)

    def test_bad_discovery_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'discovery.json'
            with patch('axure_mcp.client.discovery_path',return_value=str(path)):
                for port in ('bad', True, -1, 65536, None):
                    path.write_text(json.dumps({'port':port}))
                    self.assertIsNone(discover())
                    self.assertEqual(Bridge().port,41999)
                path.write_text(json.dumps({'port':42000}))
                self.assertEqual(Bridge().port,42000)


if __name__ == '__main__': unittest.main()
