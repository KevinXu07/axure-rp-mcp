using System.Runtime.InteropServices;
using System.Text;

namespace AxureBridge;

/// <summary>
/// Finds and closes modal dialogs belonging to this process.
///
/// Axure reports an escaped UI exception by showing a modal dialog (the message is the obfuscated
/// wrapper `iI3A.TI3Q`, the real cause is in its InnerException). While that dialog is up the UI
/// thread is blocked, so every bridge call times out and the session looks dead. Reading the dialog's
/// text gives us the actual message, and closing it hands the app back.
/// </summary>
internal static class Dialogs
{
    private const uint WM_CLOSE = 0x0010;
    private const uint BM_CLICK = 0x00F5;
    private const int GWL_STYLE = -16;
    private const int BS_DEFPUSHBUTTON = 0x0001;

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool SetWindowTextW(IntPtr hWnd, string text);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumChildWindows(IntPtr parent, EnumProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr hWnd, StringBuilder text, int max);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassNameW(IntPtr hWnd, StringBuilder text, int max);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern int GetWindowLongW(IntPtr hWnd, int index);

    [DllImport("user32.dll")]
    private static extern bool PostMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern IntPtr GetWindow(IntPtr hWnd, uint cmd);

    private delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

    private static readonly uint MyPid = (uint)Environment.ProcessId;

    public static List<(IntPtr Handle, string Title, string Text)> Find()
    {
        var found = new List<(IntPtr, string, string)>();
        if (!OperatingSystem.IsWindows()) return found;
        EnumWindows((hWnd, _) =>
        {
            try
            {
                GetWindowThreadProcessId(hWnd, out var pid);
                if (pid != MyPid || !IsWindowVisible(hWnd)) return true;
                var cls = new StringBuilder(256);
                GetClassNameW(hWnd, cls, cls.Capacity);
                var title = ReadText(hWnd);
                if (!LooksLikeDialog(cls.ToString(), hWnd, title)) return true;
                // the main document window also matches the heuristics above (WinForms class,
                // owned, has button children) - but it has hundreds of child controls while a
                // dialog has a handful
                if (CountChildren(hWnd) > 100) return true;

                found.Add((hWnd, title, CollectChildText(hWnd)));
            }
            catch { }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    private static int CountChildren(IntPtr hWnd)
    {
        var count = 0;
        EnumChildWindows(hWnd, (_, _) =>
        {
            count++;
            return count <= 200;
        }, IntPtr.Zero);
        return count;
    }

    /// <summary>
    /// Windows whose title contains `title` (visible, this process) - for flows that know the
    /// exact dialog, e.g. the WinForms "Create component" prompt which is not a #32770 dialog and
    /// therefore never matches LooksLikeDialog.
    /// </summary>
    public static List<(IntPtr Handle, string Title, string Text)> FindByTitle(string title)
    {
        var found = new List<(IntPtr, string, string)>();
        if (!OperatingSystem.IsWindows() || string.IsNullOrEmpty(title)) return found;
        EnumWindows((hWnd, _) =>
        {
            try
            {
                GetWindowThreadProcessId(hWnd, out var pid);
                if (pid != MyPid || !IsWindowVisible(hWnd)) return true;
                var windowTitle = ReadText(hWnd);
                if (!windowTitle.Contains(title, StringComparison.OrdinalIgnoreCase)) return true;
                found.Add((hWnd, windowTitle, CollectChildText(hWnd)));
            }
            catch { }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static void Close(IntPtr hWnd)
    {
        try { PostMessageW(hWnd, WM_CLOSE, IntPtr.Zero, IntPtr.Zero); }
        catch { }
    }

    /// <summary>
    /// Completes a dialog instead of cancelling it: fills the first visible edit field (e.g. the
    /// "Create component" name prompt) and clicks its default button. WinForms buttons render their
    /// captions (GetWindowTextW is empty), so OK is identified by the BS_DEFPUSHBUTTON style bit,
    /// falling back to the rightmost visible button.
    /// Returns false when no button matched, in which case the caller should fall back to Close.
    /// </summary>
    public static bool Confirm(IntPtr hWnd, string text)
    {
        IntPtr? edit = null;
        IntPtr? defaultButton = null;
        IntPtr rightmost = IntPtr.Zero;
        var rightmostRight = int.MinValue;
        try
        {
            EnumChildWindows(hWnd, (child, _) =>
            {
                var c = new StringBuilder(128);
                GetClassNameW(child, c, c.Capacity);
                var cls = c.ToString();
                if (edit == null && cls.Contains("edit", StringComparison.OrdinalIgnoreCase)
                    && IsWindowVisible(child))
                    edit = child;
                if (cls.Contains("button", StringComparison.OrdinalIgnoreCase)
                    && IsWindowVisible(child))
                {
                    if ((GetWindowLongW(child, GWL_STYLE) & BS_DEFPUSHBUTTON) != 0)
                        defaultButton ??= child;
                    GetWindowRect(child, out var rect);
                    if (rect.Right > rightmostRight)
                    {
                        rightmost = child;
                        rightmostRight = rect.Right;
                    }
                }
                return true;
            }, IntPtr.Zero);
            var ok = defaultButton ?? rightmost;
            if (edit != null && !string.IsNullOrEmpty(text))
                SetWindowTextW(edit.Value, text);
            if (ok == IntPtr.Zero) return false;
            PostMessageW(ok, BM_CLICK, IntPtr.Zero, IntPtr.Zero);
            return true;
        }
        catch { return false; }
    }

    /// <summary>
    /// Completes a custom-drawn modal prompt (e.g. "Create component", whose Continue button is
    /// painted - no Win32 children to click). Sends Enter to the focused child, to the drawing
    /// surface, then synthesises a mouse click on the dialog's bottom-right where the Continue
    /// button sits. `attempt` varies the strategy across watcher polls.
    /// </summary>
    public static bool PressEnter(IntPtr hWnd, int attempt)
    {
        try
        {
            const uint wmKeyDown = 0x0100;
            const uint wmKeyUp = 0x0101;
            const uint wmChar = 0x0102;
            const uint vkReturn = 0x0D;
            IntPtr target = hWnd;
            if (attempt % 3 != 1)
            {
                // send to the focused child / drawing surface instead of the top-level window
                GetWindowThreadProcessId(hWnd, out var tid);
                var info = new GuiThreadInfo { cbSize = Marshal.SizeOf<GuiThreadInfo>() };
                if (GetGUIThreadInfo(tid, ref info) && info.focusHwnd != IntPtr.Zero)
                    target = info.focusHwnd;
                foreach (var child in Children(hWnd))
                {
                    if (child.Class.Contains("Drawing", StringComparison.OrdinalIgnoreCase))
                        target = child.Handle;
                }
            }
            PostMessageW(target, wmKeyDown, (IntPtr)vkReturn, IntPtr.Zero);
            PostMessageW(target, wmChar, (IntPtr)vkReturn, IntPtr.Zero);
            PostMessageW(target, wmKeyUp, (IntPtr)vkReturn, IntPtr.Zero);
            if (attempt % 3 == 0)
            {
                // synthesise a click on the bottom-right of the drawing surface (Continue button)
                foreach (var child in Children(hWnd))
                {
                    if (!child.Class.Contains("Drawing", StringComparison.OrdinalIgnoreCase))
                        continue;
                    GetWindowRect(child.Handle, out var rect);
                    var x = rect.Right - rect.Left - 30;
                    var y = rect.Bottom - rect.Top - 20;
                    var lp = (IntPtr)((y << 16) | (x & 0xFFFF));
                    PostMessageW(child.Handle, 0x0200 /*WM_MOUSEMOVE*/, 0, lp);
                    PostMessageW(child.Handle, 0x0201 /*WM_LBUTTONDOWN*/, 1, lp);
                    PostMessageW(child.Handle, 0x0202 /*WM_LBUTTONUP*/, 0, lp);
                }
            }
            return true;
        }
        catch { return false; }
    }

    private sealed class ChildInfo
    {
        public IntPtr Handle;
        public string Class = "";
    }

    private static List<ChildInfo> Children(IntPtr hWnd)
    {
        var outList = new List<ChildInfo>();
        EnumChildWindows(hWnd, (child, _) =>
        {
            var c = new StringBuilder(128);
            GetClassNameW(child, c, c.Capacity);
            outList.Add(new ChildInfo { Handle = child, Class = c.ToString() });
            return true;
        }, IntPtr.Zero);
        return outList;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct GuiThreadInfo
    {
        public int cbSize;
        public uint flags;
        public IntPtr activeHwnd;
        public IntPtr focusHwnd;
        public IntPtr captureHwnd;
        public IntPtr menuOwner;
        public IntPtr moveSizeHwnd;
        public IntPtr systemMenuHwnd;
        public IntPtr caretHwnd;
    }

    [DllImport("user32.dll")]
    private static extern bool GetGUIThreadInfo(uint idThread, ref GuiThreadInfo info);

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    private static bool LooksLikeDialog(string cls, IntPtr hWnd, string title)
    {
        if (cls == "#32770") return true;                       // classic Win32 dialog
        if (title.Contains("错误") || title.Contains("Error") || title.Contains("Exception"))
            return true;
        // WPF error windows: visible, has an owner, and contains a button child
        var hasButton = false;
        EnumChildWindows(hWnd, (child, _) =>
        {
            var c = new StringBuilder(128);
            GetClassNameW(child, c, c.Capacity);
            if (c.ToString().Contains("Button")) { hasButton = true; return false; }
            return true;
        }, IntPtr.Zero);
        if (!hasButton) return false;
        var owner = GetWindow(hWnd, 4 /* GW_OWNER */);
        return owner != IntPtr.Zero && !title.Contains("Axure RP");
    }

    private static string ReadText(IntPtr hWnd)
    {
        var sb = new StringBuilder(512);
        GetWindowTextW(hWnd, sb, sb.Capacity);
        return sb.ToString();
    }

    private static string CollectChildText(IntPtr parent)
    {
        var parts = new List<string>();
        EnumChildWindows(parent, (child, _) =>
        {
            var text = ReadText(child);
            if (!string.IsNullOrWhiteSpace(text) && !parts.Contains(text)) parts.Add(text);
            return true;
        }, IntPtr.Zero);
        return string.Join(" | ", parts);
    }
}
