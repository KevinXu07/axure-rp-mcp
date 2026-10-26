using System.Reflection;

namespace AxureBridge;

/// <summary>
/// Catches exceptions that escape onto Axure's UI dispatcher.
///
/// Axure wraps an escaped exception in its own `iI3A.TI3Q` (a window-level wrapper whose real cause
/// lives in InnerException) and then shows a modal error dialog. That dialog blocks the UI thread, so
/// every later bridge call times out and the session looks dead. Installing a dispatcher handler and
/// marking the event handled keeps the app responsive and gives us the actual cause to log.
/// </summary>
internal static class ExceptionGuard
{
    private static readonly List<string> Recent = new();
    private static readonly object Gate = new();
    private static bool _installed;

    public static void Install()
    {
        if (_installed) return;
        try
        {
            var appType = Type.GetType("System.Windows.Application, PresentationFramework");
            var app = appType?.GetProperty("Current", BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
            if (app == null)
            {
                Quiet("guard: no WPF application yet");
                return;
            }
            var dispatcher = appType.GetProperty("Dispatcher", BindingFlags.Public | BindingFlags.Instance)
                ?.GetValue(app);
            if (dispatcher == null)
            {
                Quiet("guard: no dispatcher");
                return;
            }

            var evt = dispatcher.GetType().GetEvent("UnhandledException");
            if (evt == null)
            {
                Quiet("guard: no UnhandledException event");
                return;
            }

            var handler = Delegate.CreateDelegate(
                evt.EventHandlerType,
                typeof(ExceptionGuard).GetMethod(nameof(OnUnhandled),
                    BindingFlags.NonPublic | BindingFlags.Static)!);
            evt.AddEventHandler(dispatcher, handler);
            _installed = true;
            Bridge.Log("guard: dispatcher unhandled-exception handler installed");
        }
        catch (Exception ex)
        {
            Bridge.Log("guard: install failed: " + ex.Message);
        }
    }

    private static string _lastQuiet;

    /// <summary>Log a repeated probe result once instead of on every retry.</summary>
    private static void Quiet(string message)
    {
        if (_lastQuiet == message) return;
        _lastQuiet = message;
        Bridge.Log(message);
    }

    /// <summary>Dispatcher.UnhandledException(object sender, DispatcherUnhandledExceptionEventArgs e).</summary>
    private static void OnUnhandled(object sender, object args)
    {
        try
        {
            var ex = args?.GetType().GetProperty("Exception")?.GetValue(args) as Exception;
            var text = Describe(ex);
            lock (Gate)
            {
                Recent.Add(text);
                if (Recent.Count > 20) Recent.RemoveAt(0);
            }
            Bridge.Log("GUARD swallowed: " + text.Split('\n')[0]);

            // e.Handled = true -> no modal dialog, the app keeps running
            args?.GetType().GetProperty("Handled")?.SetValue(args, true);
        }
        catch (Exception inner)
        {
            Bridge.Log("guard: handler itself failed: " + inner.Message);
        }
    }

    /// <summary>Unwrap TI3Q / TargetInvocationException until something informative is left.</summary>
    private static string Describe(Exception ex)
    {
        var lines = new List<string>();
        var depth = 0;
        while (ex != null && depth++ < 6)
        {
            lines.Add($"{ex.GetType().FullName}: {ex.Message}");
            if (ex is TargetInvocationException && ex.InnerException != null)
            {
                ex = ex.InnerException;
                continue;
            }
            if (ex.GetType().FullName == "iI3A.TI3Q" && ex.InnerException != null)
            {
                lines.Add("  -> iI3A.TI3Q is Axure's window wrapper; unwrapping:");
                ex = ex.InnerException;
                continue;
            }
            break;
        }
        if (ex?.StackTrace != null)
            lines.Add(ex.StackTrace.Split('\n').FirstOrDefault()?.Trim() ?? "");
        return string.Join("\n", lines);
    }

    public static string[] Snapshot()
    {
        lock (Gate) return Recent.ToArray();
    }
}
