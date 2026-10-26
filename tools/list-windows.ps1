param([int]$ProcessId, [switch]$ScreenshotAll)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class Win {
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static List<string> List(uint want) {
    var found = new List<string>();
    EnumWindows((h, p) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid != want) return true;
      var t = new StringBuilder(512); GetWindowTextW(h, t, 512);
      var c = new StringBuilder(256); GetClassNameW(h, c, 256);
      RECT r; GetWindowRect(h, out r);
      found.Add(string.Format("{0}|{1}|{2}|{3}|{4},{5},{6},{7}", h.ToInt64(), IsWindowVisible(h), c, t,
        r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top));
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
"@
$rows = [Win]::List([uint32]$ProcessId)
foreach ($row in $rows) {
  $f = $row -split '\|'
  Write-Output ("hwnd={0,-10} visible={1,-5} class={2,-28} title={3,-40} rect={4}" -f $f[0], $f[1], $f[2], $f[3], $f[4])
}

if ($ScreenshotAll) {
  $target = $rows | ForEach-Object { $_ -split '\|' } | Where-Object { $_[1] -eq 'True' -and $_[3] -match 'Axure' } | Select-Object -First 1
}
