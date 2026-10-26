import errno
import os
import unittest
from unittest.mock import patch
from axure_mcp import launcher, paths


class LauncherTests(unittest.TestCase):
    def test_macos_executable_and_bundle_override(self):
        with patch('sys.platform','darwin'), patch.dict(os.environ,{},clear=True):
            self.assertEqual(launcher.exe_path(),'/Applications/Axure RP 11.app/Contents/MacOS/Axure RP 11')
            os.environ['AXURE_EXE']='/Applications/Axure RP 11.app'
            self.assertEqual(launcher.exe_path(),os.path.join('/Applications/Axure RP 11.app','Contents','MacOS','Axure RP 11'))
            self.assertTrue(paths.data_dir().endswith(os.path.join('Library','Application Support','Axure')))

    def test_macos_bridge_never_selects_windows_dist(self):
        with patch('sys.platform','darwin'), patch.dict(os.environ,{},clear=True), patch('os.path.exists',return_value=True), patch('os.path.getmtime',return_value=1):
            self.assertIn('net8.0',launcher.bridge_path())

    def test_posix_pid_liveness(self):
        with patch('sys.platform','darwin'):
            self.assertFalse(launcher._pid_alive(0))
            with patch('os.kill',return_value=None): self.assertTrue(launcher._pid_alive(123))
            with patch('os.kill',side_effect=ProcessLookupError(errno.ESRCH,'gone')): self.assertFalse(launcher._pid_alive(123))
            with patch('os.kill',side_effect=PermissionError(errno.EPERM,'denied')): self.assertTrue(launcher._pid_alive(123))


if __name__=='__main__': unittest.main()
