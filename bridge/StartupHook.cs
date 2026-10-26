// Loaded by the CLR via DOTNET_STARTUP_HOOKS before Axure's Main() runs.
// The runtime looks up a type literally named "StartupHook" in the GLOBAL namespace with a
// public static void Initialize() — do not put this in a namespace.
//
// It must never throw: an exception here aborts process startup.
using System;

public static class StartupHook
{
    public static void Initialize()
    {
        try
        {
            AxureBridge.Bridge.Start();
        }
        catch (Exception ex)
        {
            try { AxureBridge.Bridge.Log("startup hook failed: " + ex); } catch { }
        }
    }
}
