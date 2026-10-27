import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from axure_mcp import launcher, paths


class LauncherTests(unittest.TestCase):
    def test_macos_executable_and_bundle_override(self):
        with patch('sys.platform','darwin'), patch.dict(os.environ,{},clear=True):
            self.assertEqual(launcher.exe_path(),'/Applications/Axure RP 11.app/Contents/MacOS/Axure RP 11')
            os.environ['AXURE_EXE']='/Applications/Axure RP 11.app'
            self.assertEqual(launcher.exe_path(),os.path.join('/Applications/Axure RP 11.app','Contents','MacOS','Axure RP 11'))
            os.environ['AXURE_EXE']='/Applications/Axure RP 11.app/'
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

    def test_macos_launch_detaches_from_terminal_session(self):
        bridge=Mock()
        with tempfile.TemporaryDirectory() as tmp:
            document=Path(tmp)/'test.rp';document.write_bytes(b'rp')
            realpath=str(document.resolve())
            with patch('sys.platform','darwin'), \
                    patch.dict(os.environ,{},clear=True), \
                    patch.object(launcher,'bridge_alive',return_value=None), \
                    patch.object(launcher,'exe_path',return_value='/Applications/Axure RP 11.app/Contents/MacOS/Axure RP 11'), \
                    patch.object(launcher,'bridge_path',return_value='/tmp/AxureBridge.dll'), \
                    patch.object(launcher.os.path,'exists',return_value=True), \
                    patch.object(launcher.subprocess,'Popen') as popen, \
                    patch.object(launcher,'MAC_LAUNCH_STABILITY_SECONDS',0), \
                    patch.object(launcher,'_ping_port',return_value=True), \
                    patch.object(launcher,'wait_for_bridge',return_value=bridge):
                popen.return_value.poll.return_value=None
                self.assertIs(launcher.launch(str(document)),bridge)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        env=popen.call_args.kwargs['env']
        self.assertEqual(env['DOTNET_STARTUP_HOOKS'],'/tmp/AxureBridge.dll')
        self.assertEqual(env['AXURE_BRIDGE_PORT'],'41999')
        self.assertEqual(popen.call_args.args[0][-1],realpath)

    def test_macos_retries_only_after_an_early_process_exit(self):
        bridge=Mock();failed=Mock();healthy=Mock()
        failed.poll.return_value=1;healthy.poll.return_value=None
        with tempfile.TemporaryDirectory() as tmp:
            document=Path(tmp)/'test.rp';document.write_bytes(b'rp')
            with patch('sys.platform','darwin'), \
                    patch.dict(os.environ,{},clear=True), \
                    patch.object(launcher,'bridge_alive',return_value=None), \
                    patch.object(launcher,'exe_path',return_value='/tmp/Axure'), \
                    patch.object(launcher,'bridge_path',return_value='/tmp/AxureBridge.dll'), \
                    patch.object(launcher.os.path,'exists',return_value=True), \
                    patch.object(launcher.subprocess,'Popen',side_effect=[failed,healthy]) as popen, \
                    patch.object(launcher,'MAC_LAUNCH_STABILITY_SECONDS',0), \
                    patch.object(launcher,'MAC_LAUNCH_RETRY_DELAY_SECONDS',0), \
                    patch.object(launcher.time,'sleep'), \
                    patch.object(launcher,'_ping_port',return_value=True), \
                    patch.object(launcher,'wait_for_bridge',return_value=bridge):
                self.assertIs(launcher.launch(str(document)),bridge)
        self.assertEqual(popen.call_count,2)

    def test_macos_retries_when_bridge_connection_lost_during_startup(self):
        bridge=Mock();failed=Mock();healthy=Mock()
        failed.poll.side_effect=[None,None,None,1]
        healthy.poll.return_value=None
        with tempfile.TemporaryDirectory() as tmp:
            document=Path(tmp)/'test.rp';document.write_bytes(b'rp')
            with patch('sys.platform','darwin'), \
                    patch.dict(os.environ,{},clear=True), \
                    patch.object(launcher,'bridge_alive',return_value=None), \
                    patch.object(launcher,'exe_path',return_value='/tmp/Axure'), \
                    patch.object(launcher,'bridge_path',return_value='/tmp/AxureBridge.dll'), \
                    patch.object(launcher.os.path,'exists',return_value=True), \
                    patch.object(launcher.subprocess,'Popen',side_effect=[failed,healthy]), \
                    patch.object(launcher,'MAC_LAUNCH_STABILITY_SECONDS',0), \
                    patch.object(launcher,'MAC_LAUNCH_RETRY_DELAY_SECONDS',0), \
                    patch.object(launcher.time,'sleep'), \
                    patch.object(launcher,'_ping_port',return_value=True), \
                    patch.object(launcher,'wait_for_bridge',
                                 side_effect=[launcher.BridgeError('gone'),bridge]):
                self.assertIs(launcher.launch(str(document)),bridge)

    def test_launch_rejects_missing_or_non_rp_file_before_spawn(self):
        with patch.object(launcher,'bridge_alive',return_value=None), \
                patch.object(launcher.subprocess,'Popen') as popen:
            with self.assertRaises(FileNotFoundError):
                launcher.launch('/tmp/axure-missing-file.rp')
            popen.assert_not_called()
        with patch.object(launcher,'bridge_alive',return_value=None), \
                patch.object(launcher.os.path,'isfile',return_value=True), \
                patch.object(launcher.subprocess,'Popen') as popen:
            with self.assertRaises(ValueError):
                launcher.launch('/tmp/not-an-axure-document.txt')
            popen.assert_not_called()


if __name__=='__main__': unittest.main()
