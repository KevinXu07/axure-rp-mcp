import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from axure_mcp import lock


class LockTests(unittest.TestCase):
    def test_nested_and_cross_thread_timeout(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(lock,'LOCK_PATH',os.path.join(tmp,'lock')):
            errors=[]
            def contender():
                try:
                    with lock.bridge_lock(timeout=0.05): errors.append('entered concurrently')
                except TimeoutError: pass
            with lock.bridge_lock(), lock.bridge_lock():
                t=threading.Thread(target=contender);t.start();t.join(2)
                self.assertFalse(t.is_alive())
                self.assertEqual(errors,[])
            with lock.bridge_lock(timeout=0.1): pass

    def test_separate_process_exclusion_nonempty_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(lock,'LOCK_PATH',os.path.join(tmp,'lock')):
            with open(lock.LOCK_PATH,'wb') as fh: fh.write(b'nonempty stale lock file')
            script = '''import sys
from axure_mcp import lock
lock.LOCK_PATH=sys.argv[1]
try:
 with lock.bridge_lock(timeout=0.1): sys.exit(2)
except TimeoutError: sys.exit(0)
'''
            with lock.bridge_lock():
                r=subprocess.run([sys.executable,'-c',script,lock.LOCK_PATH],timeout=5)
                self.assertEqual(r.returncode,0)
            with lock.bridge_lock(timeout=0.1): pass


if __name__ == '__main__': unittest.main()
