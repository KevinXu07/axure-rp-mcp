using System.Runtime.InteropServices;
using System.Text;

namespace AxureBridge;

/// <summary>
/// Suppresses painting on Axure's main window while the document is being mutated.
///
/// Axure's canvas paints by enumerating its object list. A bridge call that inserts a widget triggers
/// a repaint from inside the call, and that repaint then enumerates the very list being changed:
///
///   System.InvalidOperationException: Collection was modified; enumeration operation may not execute.
///     at UtNY.htNZ.OnPaint(...)  at NI3S.aI3N.S39X(IntPtr, UInt32, IntPtr, IntPtr)   // WM_PAINT
///
/// Axure's global handler turns that into its "Report Error" dialog (the iI3A.TI3Q wrapper). The edit
/// itself succeeds, but the dialogs pile up and can block the UI thread. Turning redraw off for the
/// duration of a mutation and invalidating afterwards avoids the re-entrancy entirely.
/// </summary>
internal static class PaintGuard
{
    private const uint WM_SETREDRAW = 0x000B;

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr hWnd, StringBuilder text, int max);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool InvalidateRect(IntPtr hWnd, IntPtr rect, bool erase);

    [DllImport("user32.dll")]
    private static extern bool UpdateWindow(IntPtr hWnd);

    private delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

    private static readonly uint MyPid = (uint)Environment.ProcessId;
    private static IntPtr _main;
    private static int _depth;
    private static volatile bool _dirty;
    private static readonly object Gate = new();

    public static void Pause()
    {
        if (!OperatingSystem.IsWindows()) return;
        lock (Gate)
        {
            if (_depth++ > 0) return;
            var hwnd = MainWindow();
            if (hwnd == IntPtr.Zero) return;
            SendMessageW(hwnd, WM_SETREDRAW, IntPtr.Zero, IntPtr.Zero);
        }
    }

    public static void Resume()
    {
        if (!OperatingSystem.IsWindows()) return;
        lock (Gate)
        {
            if (_depth == 0) return;
            if (--_depth > 0) return;
            var hwnd = MainWindow();
            if (hwnd == IntPtr.Zero) return;
            SendMessageW(hwnd, WM_SETREDRAW, new IntPtr(1), IntPtr.Zero);
            // Do NOT invalidate here. A full-canvas repaint after every single call is what turned an
            // 8s build into 48s (Axure repaints by enumerating every object). The caller asks for a
            // repaint explicitly with the "flush_paint" op once a batch is done.
            _dirty = true;
        }
    }

    /// <summary>Repaint the canvas once, after a batch of edits.</summary>
    public static bool Flush()
    {
        if (!OperatingSystem.IsWindows()) return false;
        if (!_dirty) return false;
        var hwnd = MainWindow();
        if (hwnd == IntPtr.Zero) return false;
        _dirty = false;
        InvalidateRect(hwnd, IntPtr.Zero, false);
        UpdateWindow(hwnd);
        return true;
    }

    private static IntPtr MainWindow()
    {
        if (_main != IntPtr.Zero) return _main;
        IntPtr best = IntPtr.Zero;
        var bestLen = 0;
        EnumWindows((hWnd, _) =>
        {
            try
            {
                GetWindowThreadProcessId(hWnd, out var pid);
                if (pid != MyPid || !IsWindowVisible(hWnd)) return true;
                var sb = new StringBuilder(512);
                GetWindowTextW(hWnd, sb, sb.Capacity);
                var title = sb.ToString();
                if (title.Length == 0) return true;
                if (title.Contains("Axure")) { best = hWnd; bestLen = int.MaxValue; return false; }
                if (title.Length > bestLen) { best = hWnd; bestLen = title.Length; }
            }
            catch { }
            return true;
        }, IntPtr.Zero);
        _main = best;
        return best;
    }
}
