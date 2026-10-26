param(
    [int]$ProcessId,
    [string]$Out = (Join-Path (Split-Path -Parent $PSScriptRoot) 'analysis\shot-window.png'),
    [int]$WaitMs = 900,
    [long]$Hwnd = 0
)
# Raise one of a process's windows above everything else, capture it, then restore z-order.
#
# Axure's main window reports no MainWindowHandle (its title is a single character and it is drawn
# by the app itself), so pick the largest visible top-level window of the process instead.
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class W2 {
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static readonly IntPtr TOPMOST = new IntPtr(-1);
  public static readonly IntPtr NOTOPMOST = new IntPtr(-2);

  public static IntPtr Biggest(uint want) {
    IntPtr best = IntPtr.Zero; long bestArea = 0;
    EnumWindows((h, p) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid != want || !IsWindowVisible(h)) return true;
      RECT r; GetWindowRect(h, out r);
      long area = (long)(r.Right - r.Left) * (r.Bottom - r.Top);
      if (area > bestArea) { bestArea = area; best = h; }
      return true;
    }, IntPtr.Zero);
    return best;
  }
}
"@
$h = if ($Hwnd -ne 0) { [IntPtr]$Hwnd } else { [W2]::Biggest([uint32]$ProcessId) }
if ($h -eq [IntPtr]::Zero) { throw "process $ProcessId has no visible window" }

$previous = [W2]::GetForegroundWindow()
if ([W2]::IsIconic($h)) { [void][W2]::ShowWindow($h, 9) }
[void][W2]::ShowWindow($h, 5)
[void][W2]::BringWindowToTop($h)
[void][W2]::SetWindowPos($h, [W2]::TOPMOST, 0, 0, 0, 0, 0x0003)
[void][W2]::SetForegroundWindow($h)
Start-Sleep -Milliseconds $WaitMs

$r = New-Object W2+RECT
[void][W2]::GetWindowRect($h, [ref]$r)
$w = $r.Right - $r.Left; $hh = $r.Bottom - $r.Top
if ($w -le 0 -or $hh -le 0) { throw "bad window rect ${w}x${hh}" }
$bmp = New-Object System.Drawing.Bitmap($w, $hh)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()

[void][W2]::SetWindowPos($h, [W2]::NOTOPMOST, 0, 0, 0, 0, 0x0003)
if ($previous -ne [IntPtr]::Zero) { [void][W2]::SetForegroundWindow($previous) }
Write-Output "hwnd=$($h.ToInt64()) rect=$($r.Left),$($r.Top) ${w}x${hh} -> $Out"
