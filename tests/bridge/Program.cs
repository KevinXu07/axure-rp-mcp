using System.Diagnostics;
using System.Reflection;
using AxureBridge;

static class Program
{
    static void Set(string name, object value) => typeof(UiThread)
        .GetField(name, BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, value);

    public static void Main()
    {
        var marshaller = new TestMarshaller();
        Set("_resolved", true);
        Set("_appMarshaller", marshaller);
        Set("_appMarshal", typeof(TestMarshaller).GetMethod(nameof(TestMarshaller.Enqueue)));
        var watch = Stopwatch.StartNew();
        var result = UiThread.Run<object>(() => null);
        if (result != null || watch.Elapsed > TimeSpan.FromSeconds(2))
            throw new Exception("null result must complete without a 15 second wait");
        if (UiThread.Run(() => 42) != 42) throw new Exception("value result lost");
        try
        {
            UiThread.Run<object>(() => throw new InvalidOperationException("expected"));
            throw new Exception("exception not propagated");
        }
        catch (InvalidOperationException ex) when (ex.Message == "expected") { }
        marshaller.Hold = true;
        var ranLate = false;
        try
        {
            UiThread.Run(() => { ranLate = true; return 1; });
            throw new Exception("queued operation did not time out");
        }
        catch (TimeoutException ex) when (ex.Message.Contains("cancelled before it started")) { }
        marshaller.Pending();
        if (ranLate) throw new Exception("timed-out queued mutation executed later");
        Set("_appMarshaller", null);
        Set("_appMarshal", null);
        try
        {
            UiThread.Run<object>(() => throw new Exception("worker-thread body must not run"));
            throw new Exception("missing dispatcher was not rejected");
        }
        catch (InvalidOperationException ex) when (ex.Message.Contains("dispatcher unavailable")) { }
        if (!OperatingSystem.IsWindows())
        {
            PaintGuard.Pause(); PaintGuard.Resume();
            if (PaintGuard.Flush() || Dialogs.Find().Count != 0)
                throw new Exception("non-Windows guards must be inert");
        }
        Console.WriteLine("PASS: null/value/error completion, queued timeout cancellation, missing dispatcher, platform guards");
    }
}

public class TestMarshaller
{
    public bool Hold;
    public Action Pending;
    public void Enqueue(Action action)
    {
        if (Hold) Pending = action;
        else ThreadPool.QueueUserWorkItem(_ => action());
    }
}

namespace AxureBridge
{
    public static class Bridge
    {
        public static void Log(string message) { }
        public static Type ResolveType(string name) => throw new InvalidOperationException(name);
    }
}
