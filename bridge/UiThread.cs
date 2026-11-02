using System.Reflection;

namespace AxureBridge;

/// <summary>
/// Marshals a call onto Axure's UI thread. Tries WPF's Dispatcher first, then falls back to the
/// application's own marshaller (kSnw.xSnT.iS8H().M3GD(Action)).
/// </summary>
internal static class UiThread
{
    private static volatile bool _resolved;
    private static object _dispatcher;
    private static MethodInfo _dispatcherInvoke;
    private static MethodInfo _dispatcherCheckAccess;
    private static object _appMarshaller;
    private static MethodInfo _appMarshal;
    private static PropertyInfo _invokeRequired;
    private static Type _marshalDelegateType;
    private static bool _marshalSyncFlag;
    private static readonly object Gate = new();

    public static T Run<T>(Func<T> func)
    {
        Resolve();
        object result = null;
        Exception error = null;

        void Body()
        {
            // hold off painting while the document is being mutated: Axure's OnPaint enumerates the
            // object list we are changing, which throws "Collection was modified" -> Report Error
            try
            {
                PaintGuard.Pause();
                result = func();
            }
            catch (Exception ex) { error = ex; }
            finally
            {
                try { PaintGuard.Resume(); }
                catch (Exception ex) { error ??= ex; }
            }
        }

        if (_dispatcher != null && _dispatcherInvoke != null)
        {
            var onUi = _dispatcherCheckAccess != null && (bool)_dispatcherCheckAccess.Invoke(_dispatcher, null);
            if (onUi) Body();
            else
            {
                Action action = Body;
                _dispatcherInvoke.Invoke(_dispatcher, new object[] { action, Array.Empty<object>() });
            }
        }
        else if (_appMarshaller != null && _appMarshal != null)
        {
            if (_invokeRequired != null && !(bool)_invokeRequired.GetValue(_appMarshaller)) Body();
            else
            {
                var done = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                int state = 0; // queued=0, running=1, cancelled=2
                Action action = () =>
                {
                    if (Interlocked.CompareExchange(ref state, 1, 0) != 0) return;
                    try { Body(); } finally { done.TrySetResult(true); }
                };
                // Axure's own marshaller (s95L.M3GD) takes a custom parameterless delegate
                // (pbCu.xbCw), not Action, plus an optional bool (true = blocking Invoke). Build the
                // delegate against a per-call target so the state guard above stays effective.
                object[] args;
                if (_marshalDelegateType != null)
                {
                    var slot = new MarshalSlot(action);
                    var dlg = Delegate.CreateDelegate(_marshalDelegateType, slot, nameof(MarshalSlot.Run));
                    args = _marshalSyncFlag ? new object[] { dlg, true } : new object[] { dlg };
                }
                else
                {
                    args = new object[] { action };
                }
                _appMarshal.Invoke(_appMarshaller, args);
                // Completion is independent of the result: void/null is a successful call too.
                if (!done.Task.Wait(TimeSpan.FromSeconds(15)))
                {
                    var previous = Interlocked.CompareExchange(ref state, 2, 0);
                    throw new TimeoutException(previous == 0
                        ? "Axure UI operation cancelled before it started"
                        : "Axure UI operation is still running; inspect the document before retrying");
                }
            }
        }
        else
        {
            throw new InvalidOperationException("Axure UI dispatcher unavailable; refusing to access the document on a worker thread");
        }

        if (error != null) throw error;
        return result is T t ? t : default;
    }

    private static void Resolve()
    {
        if (_resolved) return;
        lock (Gate)
        {
            if (_resolved) return;
            ResolveWpf();
            if (_dispatcherInvoke == null)
            {
                if (OperatingSystem.IsMacOS()) ResolveCocoa();
                else ResolveAppMarshaller();
            }
            _resolved = _dispatcherInvoke != null || _appMarshal != null;
            Bridge.Log($"uithread: dispatcher={( _dispatcher != null)} appMarshaller={(_appMarshaller != null)}");
        }
    }

    private static void ResolveCocoa()
    {
        try
        {
            var t = Bridge.ResolveType("MCocoa.NSApplication");
            _appMarshaller = t.GetMethod("sharedApplication", Type.EmptyTypes)?.Invoke(null, null);
            _appMarshal = t.GetMethod("BeginInvoke", new[] { typeof(Action) });
            _invokeRequired = t.GetProperty("InvokeRequired");
        }
        catch (Exception ex) { Bridge.Log("Cocoa dispatcher probe: " + ex.Message); }
    }

    private static void ResolveWpf()
    {
        try
        {
            var appType = Type.GetType("System.Windows.Application, PresentationFramework");
            var current = appType?.GetProperty("Current", BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
            if (current == null) return;

            _dispatcher = appType.GetProperty("Dispatcher", BindingFlags.Public | BindingFlags.Instance)?.GetValue(current);
            if (_dispatcher == null) return;

            var dispatcherType = _dispatcher.GetType();
            _dispatcherCheckAccess = dispatcherType.GetMethod("CheckAccess", Type.EmptyTypes);
            foreach (var m in dispatcherType.GetMethods(BindingFlags.Public | BindingFlags.Instance))
            {
                if (m.Name != "Invoke") continue;
                var ps = m.GetParameters();
                if (ps.Length == 2 && ps[0].ParameterType == typeof(Delegate) && ps[1].ParameterType == typeof(object[]))
                {
                    _dispatcherInvoke = m;
                    break;
                }
            }
        }
        catch (Exception ex) { Bridge.Log("wpf dispatcher probe: " + ex.Message); }
    }

    private static void ResolveAppMarshaller()
    {
        try
        {
            var t = Bridge.ResolveType("kSnw.xSnT");
            var getter = t.GetMethod("iS8H", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            _appMarshaller = getter?.Invoke(null, null);
            if (_appMarshaller == null) return;

            // s95L.M3GD(xbCw run, bool sync = true): xbCw is Axure's own parameterless void
            // delegate, and the bool selects Control.Invoke (true) vs BeginInvoke (false).
            var m = _appMarshaller.GetType()
                .GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
                .Where(x => x.Name == "M3GD")
                .OrderBy(x => x.GetParameters().Length)
                .FirstOrDefault();
            var ps = m?.GetParameters();
            if (ps == null || ps.Length == 0 || !typeof(Delegate).IsAssignableFrom(ps[0].ParameterType)) return;
            _marshalDelegateType = ps[0].ParameterType;
            _marshalSyncFlag = ps.Length > 1 && ps[1].ParameterType == typeof(bool);
            _appMarshal = m;
        }
        catch (Exception ex) { Bridge.Log("app marshaller probe: " + ex.Message); }
    }

    private sealed class MarshalSlot
    {
        public readonly Action Step;
        public MarshalSlot(Action step) { Step = step; }
        public void Run() { Step(); }
    }
}
