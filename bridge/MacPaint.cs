using System.Collections;
using System.Reflection;

namespace AxureBridge;

/// <summary>Invalidate the native Cocoa view tree without reloading document objects.</summary>
internal static class MacPaint
{
    public static bool Flush()
    {
        var appType = Bridge.ResolveType("MCocoa.NSApplication");
        var app = appType.GetMethod("sharedApplication", Type.EmptyTypes)?.Invoke(null, null);
        if (app == null) return false;
        var windows = app.GetType().GetMethod("windows", Type.EmptyTypes)?.Invoke(app, null) as IEnumerable;
        if (windows == null) return false;
        var painted = false;
        foreach (var window in windows)
        {
            var view = window.GetType().GetMethod("contentView", Type.EmptyTypes)?.Invoke(window, null);
            if (view == null) continue;
            Invalidate(view);
            window.GetType().GetMethod("displayIfNeeded", Type.EmptyTypes)?.Invoke(window, null);
            painted = true;
        }
        return painted;
    }

    private static void Invalidate(object view)
    {
        var type = view.GetType();
        type.GetMethod("setNeedsDisplay", new[] { typeof(bool) })?.Invoke(view, new object[] { true });
        if (type.GetMethod("subviews", Type.EmptyTypes)?.Invoke(view, null) is IEnumerable children)
            foreach (var child in children) Invalidate(child);
    }
}
