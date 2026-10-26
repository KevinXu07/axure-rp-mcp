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
    private const int GWL_STYLE = -16;

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
    private static extern bool PostMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern IntPtr GetWindow(IntPtr hWnd, uint cmd);

    private delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

    private static readonly uint MyPid = (uint)Environment.ProcessId;

    public static List<(IntPtr Handle, string Title, string Text)> Find()
    {
        var found = new List<(IntPtr, string, string)>();
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

                found.Add((hWnd, title, CollectChildText(hWnd)));
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
