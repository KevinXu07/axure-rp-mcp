using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Runtime.Loader;

namespace AxureBridge;

/// <summary>
/// In-process reflection bridge. Listens on 127.0.0.1:$AXURE_BRIDGE_PORT (default 41999) and speaks
/// newline-delimited JSON so an external process (or an MCP server) can drive Axure's live document model.
/// </summary>
public static class Bridge
{
    public const int DefaultPort = 41999;

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = false,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    private static TcpListener _listener;
    private static int _port;
    private static readonly object _logLock = new();
    private static string _logPath;

    public static void Start()
    {
        _logPath = Path.Combine(Path.GetTempPath(), "axure-bridge.log");
        Log($"boot pid={Environment.ProcessId} version={Environment.Version}");

        _port = int.TryParse(Environment.GetEnvironmentVariable("AXURE_BRIDGE_PORT"), out var p) ? p : DefaultPort;

        // asm resolve: our own folder + the app folder (we only touch framework types, but be safe)
        AssemblyLoadContext.Default.Resolving += (ctx, name) =>
        {
            foreach (var dir in CandidateDirs())
            {
                var candidate = Path.Combine(dir, name.Name + ".dll");
                if (File.Exists(candidate)) return ctx.LoadFromAssemblyPath(candidate);
            }
            return null;
        };

        var thread = new Thread(ListenLoop) { IsBackground = true, Name = "AxureBridge" };
        thread.Start();
        Log($"listening on 127.0.0.1:{_port}");

        // UI may not exist yet at startup; retry briefly on a background thread
        new Thread(() =>
        {
            for (var i = 0; i < 40; i++)
            {
                ExceptionGuard.Install();
                Thread.Sleep(500);
            }
            Log("guard: WPF dispatcher unavailable (Axure runs its own message loop); "
                + "modal dialogs are handled by dismissing them instead");
        }) { IsBackground = true, Name = "AxureBridgeGuard" }.Start();
    }

    private static IEnumerable<string> CandidateDirs()
    {
        yield return AppContext.BaseDirectory;
        var entry = Assembly.GetEntryAssembly()?.Location;
        if (!string.IsNullOrEmpty(entry)) yield return Path.GetDirectoryName(entry);
    }

    public static void Log(string message)
    {
        try
        {
            lock (_logLock)
            {
                if (_logPath != null)
                    File.AppendAllText(_logPath, $"{DateTime.Now:HH:mm:ss.fff} {message}{Environment.NewLine}");
            }
        }
        catch { }
    }

    private static void ListenLoop()
    {
        var attempt = 0;
        while (true)
        {
            try
            {
                _listener = new TcpListener(IPAddress.Loopback, _port + attempt);
                _listener.Start();
                _port += attempt;
                break;
            }
            catch (SocketException)
            {
                attempt++;
                if (attempt > 20)
                {
                    Log("all ports busy, giving up");
                    return;
                }
            }
        }

        WriteDiscovery();

        while (true)
        {
            TcpClient client;
            try { client = _listener.AcceptTcpClient(); }
            catch (Exception ex) { Log("accept: " + ex.Message); continue; }

            var t = new Thread(() => Serve(client)) { IsBackground = true };
            t.Start();
        }
    }

    private static void WriteDiscovery()
    {
        try
        {
            var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Axure");
            Directory.CreateDirectory(dir);
            var path = Path.Combine(dir, "axure-bridge.json");
            var payload = new JsonObject
            {
                ["port"] = _port,
                ["pid"] = Environment.ProcessId,
                ["entry"] = Assembly.GetEntryAssembly()?.GetName().Name,
                ["proc"] = Process.GetCurrentProcess().ProcessName,
                ["startedUtc"] = DateTime.UtcNow.ToString("O")
            };
            File.WriteAllText(path, payload.ToJsonString(JsonOpts));
            Log("discovery -> " + path);
        }
        catch (Exception ex) { Log("discovery failed: " + ex.Message); }
    }

    private static void Serve(TcpClient client)
    {
        using var _ = client;
        using var stream = client.GetStream();
        using var reader = new StreamReader(stream, new UTF8Encoding(false));
        using var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };

        while (true)
        {
            string line;
            try { line = reader.ReadLine(); }
            catch { return; }
            if (line == null) return;
            if (line.Length == 0) continue;

            string response;
            try
            {
                var request = JsonNode.Parse(line)?.AsObject() ?? throw new InvalidOperationException("bad request");
                var result = Dispatch(request);
                response = new JsonObject
                {
                    ["id"] = request.TryGetPropertyValue("id", out var id) ? id?.DeepClone() : null,
                    ["ok"] = true,
                    ["result"] = result
                }.ToJsonString(JsonOpts);
            }
            catch (Exception ex)
            {
                var msg = Describe(ex);
                response = new JsonObject { ["ok"] = false, ["error"] = msg }.ToJsonString(JsonOpts);
                Log("ERR " + line.Substring(0, Math.Min(200, line.Length)) + " -> " + msg.Split('\n')[0]);
            }

            try { writer.WriteLine(response); }
            catch { return; }
        }
    }

    private static JsonNode Dispatch(JsonObject req)
    {
        var op = req["op"]?.GetValue<string>() ?? throw new InvalidOperationException("missing op");
        return op switch
        {
            "ping" => Ping(),
            "types" => FindTypes(req),
            "members" => Members(req),
            "invoke" => Invoke(req),
            "dialogs" => DumpDialogs(req),
            "flush_paint" => JsonValue.Create(PaintGuard.Flush()),
            "dismiss" => DismissDialogs(req),
            "errors" => new JsonArray(ExceptionGuard.Snapshot().Select(e => (JsonNode)JsonValue.Create(e)).ToArray()),
            "ctor" => Ctor(req),
            "get" => GetMember(req),
            "set" => SetMember(req),
            "handles" => Handles(),
            "release" => Release(req),
            "docs" => Docs(),
            "dump" => Dump(req),
            "iter" => Iter(req),
            "subtypes" => Subtypes(req),
            "id" => Identity(req),
            "makedict" => MakeDict(req),
            "log" => JsonValue.Create("ok"),
            _ => throw new InvalidOperationException("unknown op: " + op)
        };
    }

    private static JsonNode Ping()
    {
        var asm = Assembly.GetEntryAssembly();
        return new JsonObject
        {
            ["pid"] = Environment.ProcessId,
            ["port"] = _port,
            ["entry"] = asm?.GetName().Name,
            ["entryPath"] = asm?.Location,
            ["clr"] = Environment.Version.ToString(),
            ["thread"] = Thread.CurrentThread.ManagedThreadId
        };
    }

    // Builds an empty Dictionary<TKey, TValue> from two short type names. Composing the full
    // assembly-qualified name of a closed generic by hand is error-prone, so the bridge does it.
    private static JsonNode MakeDict(JsonObject req)
    {
        var keyName = req["key"]?.GetValue<string>() ?? "System.String";
        var valueName = req["value"]?.GetValue<string>()
                        ?? throw new InvalidOperationException("makedict needs a value type");
        var keyType = ResolveType(keyName) ?? throw new InvalidOperationException("unknown key type: " + keyName);
        var valueType = ResolveType(valueName) ?? throw new InvalidOperationException("unknown value type: " + valueName);
        var dictType = typeof(Dictionary<,>).MakeGenericType(keyType, valueType);
        var instance = Activator.CreateInstance(dictType);
        return new JsonObject
        {
            ["$h"] = HandleTable.Add(instance),
            ["type"] = dictType.FullName,
            ["str"] = dictType.ToString()
        };
    }

    private static JsonNode FindTypes(JsonObject req)
    {
        var q = req["q"]?.GetValue<string>() ?? "";
        var limit = req["limit"]?.GetValue<int>() ?? 60;
        var exact = req["exact"]?.GetValue<bool>() ?? false;

        var hits = new List<string>();
        foreach (var asm in Assemblies())
        {
            Type[] types;
            try { types = asm.GetTypes(); }
            catch (ReflectionTypeLoadException rtle) { types = rtle.Types.Where(t => t != null).ToArray(); }
            catch { continue; }

            foreach (var t in types)
            {
                var full = t.FullName ?? t.Name;
                var ok = exact
                    ? string.Equals(t.Name, q, StringComparison.Ordinal) || string.Equals(full, q, StringComparison.Ordinal)
                    : full.Contains(q, StringComparison.OrdinalIgnoreCase);
                if (ok) hits.Add(full);
                if (hits.Count >= limit * 8) break;
            }
        }

        var arr = new JsonArray();
        foreach (var h in hits.Distinct().OrderBy(x => x).Take(limit)) arr.Add(h);
        return arr;
    }

    private static IEnumerable<Assembly> Assemblies()
    {
        var list = new List<Assembly>();
        var entry = Assembly.GetEntryAssembly();
        if (entry != null) list.Add(entry);
        foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            if (!list.Contains(asm) && !asm.IsDynamic) list.Add(asm);
        return list;
    }

    public static Type ResolveType(string name)
    {
        if (string.IsNullOrWhiteSpace(name)) throw new ArgumentException("empty type name");

        var t = Type.GetType(name, throwOnError: false);
        if (t != null) return t;

        // framework types live in assemblies the host may not have loaded yet
        // ("System.IO.Compression.GZipStream" -> load System.IO.Compression first)
        var segments = name.Split('.');
        for (var take = Math.Min(segments.Length - 1, 3); take >= 1; take--)
        {
            var assemblyName = string.Join(".", segments.Take(take));
            try { Assembly.Load(new AssemblyName(assemblyName)); }
            catch { continue; }
            t = Type.GetType(name, throwOnError: false);
            if (t != null) return t;
        }

        var cands = new List<Type>();
        foreach (var asm in Assemblies())
        {
            Type[] types;
            try { types = asm.GetTypes(); }
            catch (ReflectionTypeLoadException rtle) { types = rtle.Types.Where(x => x != null).ToArray(); }
            catch { continue; }
            foreach (var x in types)
                if (x.FullName == name || x.Name == name) cands.Add(x);
            if (cands.Count > 0) break;
        }

        if (cands.Count == 0)
        {
            // suffix match, e.g. "xSnT" for "kSnw.xSnT"
            foreach (var asm in Assemblies())
            {
                Type[] types;
                try { types = asm.GetTypes(); }
                catch (ReflectionTypeLoadException rtle) { types = rtle.Types.Where(x => x != null).ToArray(); }
                catch { continue; }
                foreach (var x in types)
                    if (x.FullName != null && x.FullName.EndsWith("." + name, StringComparison.Ordinal)) cands.Add(x);
                if (cands.Count > 0) break;
            }
        }

        if (cands.Count == 0) throw new InvalidOperationException("type not found: " + name);
        return cands[0];
    }

    private static JsonNode Members(JsonObject req)
    {
        var type = ResolveType(req["type"]!.GetValue<string>());
        var filter = req["q"]?.GetValue<string>();
        const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;

        bool Keep(string name) => filter == null || name.Contains(filter, StringComparison.OrdinalIgnoreCase);

        var result = new JsonObject { ["type"] = type.FullName, ["base"] = type.BaseType?.FullName };

        var ctors = new JsonArray();
        foreach (var c in type.GetConstructors(flags))
            ctors.Add(Sig("ctor", c.GetParameters()));
        result["ctors"] = ctors;

        var methods = new JsonArray();
        foreach (var m in type.GetMethods(flags).Where(m => Keep(m.Name)).OrderBy(m => m.Name))
            methods.Add(Sig((m.IsStatic ? "static " : "") + m.Name, m.GetParameters(), m.ReturnType));
        result["methods"] = methods;

        var props = new JsonArray();
        foreach (var p in type.GetProperties(flags).Where(p => Keep(p.Name)))
            props.Add(Sig((p.GetMethod?.IsStatic == true ? "static " : "") + p.Name, Array.Empty<ParameterInfo>(), p.PropertyType));
        result["props"] = props;

        var fields = new JsonArray();
        foreach (var f in type.GetFields(flags).Where(f => Keep(f.Name)))
            fields.Add(new JsonObject
            {
                ["name"] = (f.IsStatic ? "static " : "") + f.Name,
                ["type"] = f.FieldType.FullName ?? f.FieldType.Name
            });
        result["fields"] = fields;

        var nested = new JsonArray();
        foreach (var n in type.GetNestedTypes(BindingFlags.Public | BindingFlags.NonPublic))
            nested.Add(n.FullName);
        result["nested"] = nested;

        return result;
    }

    private static JsonObject Sig(string name, ParameterInfo[] ps, Type ret = null)
    {
        var arr = new JsonArray();
        foreach (var p in ps) arr.Add((p.ParameterType.FullName ?? p.ParameterType.Name) + " " + p.Name);
        var o = new JsonObject { ["name"] = name, ["params"] = arr };
        if (ret != null) o["returns"] = ret.FullName ?? ret.Name;
        return o;
    }

    /// <summary>
    /// Unwraps reflection / Axure wrappers so the caller sees the real cause.
    /// TargetInvocationException nests, and iI3A.TI3Q is Axure's window-level wrapper.
    /// </summary>
    private static string Describe(Exception ex)
    {
        var parts = new List<string>();
        var depth = 0;
        while (ex != null && depth++ < 8)
        {
            var name = ex.GetType().Name;
            if (name != "TargetInvocationException")
                parts.Add($"{name}: {ex.Message}");
            if (ex.InnerException == null) break;
            ex = ex.InnerException;
        }
        if (ex?.StackTrace != null)
            parts.Add(ex.StackTrace.Split('\n').FirstOrDefault()?.Trim() ?? "");
        return string.Join("\n", parts);
    }

    private static JsonNode DumpDialogs(JsonObject req)
    {
        var list = new JsonArray();
        foreach (var (handle, title, text) in Dialogs.Find())
        {
            list.Add(new JsonObject
            {
                ["title"] = title,
                ["text"] = text,
                ["handle"] = handle.ToInt64()
            });
        }
        return list;
    }

    private static JsonNode DismissDialogs(JsonObject req)
    {
        var close = req["close"]?.GetValue<bool>() ?? true;
        var list = new JsonArray();
        foreach (var (handle, title, text) in Dialogs.Find())
        {
            list.Add(new JsonObject { ["title"] = title, ["text"] = text });
            if (close) Dialogs.Close(handle);
            Log($"dialog {(close ? "closed" : "found")}: {title} :: {text}");
        }
        return list;
    }

    private static JsonNode Invoke(JsonObject req)
    {
        var typeName = req["type"]?.GetValue<string>();
        object target = null;
        Type type;

        if (req.TryGetPropertyValue("handle", out var h) && h != null)
        {
            target = HandleTable.Get(h.GetValue<int>());
            type = target.GetType();
        }
        else
        {
            type = ResolveType(typeName ?? throw new InvalidOperationException("type or handle required"));
        }

        var methodName = req["method"]?.GetValue<string>() ?? throw new InvalidOperationException("missing method");
        var argNodes = req["args"]?.AsArray();
        var paramHints = req["params"]?.AsArray();
        var ui = req["ui"]?.GetValue<bool>() ?? true;

        Func<object> call = () =>
        {
            var (method, args) = BindMethod(type, methodName, argNodes, paramHints);
            return method.Invoke(target, args);
        };

        var result = ui ? UiThread.Run(call) : call();
        return Encode(result);
    }

    private static JsonNode Ctor(JsonObject req)
    {
        var type = ResolveType(req["type"]!.GetValue<string>());
        var argNodes = req["args"]?.AsArray();
        var paramHints = req["params"]?.AsArray();

        Func<object> create = () =>
        {
            var (ctor, args) = BindCtor(type, argNodes, paramHints);
            return ctor.Invoke(args);
        };

        var result = (req["ui"]?.GetValue<bool>() ?? true) ? UiThread.Run(create) : create();
        return Encode(result);
    }

    private static (MethodInfo, object[]) BindMethod(Type type, string name, JsonArray argNodes, JsonArray paramHints)
    {
        var n = argNodes?.Count ?? 0;
        var hintTypes = paramHints?.Select(p => ResolveType(p!.GetValue<string>())).ToArray();

        var cands = type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static)
            .Where(m => m.Name == name && m.GetParameters().Length == n)
            .ToList();

        if (cands.Count == 0)
            throw new InvalidOperationException($"method {type.FullName}.{name} with {n} args not found");

        Exception last = null;
        foreach (var m in cands)
        {
            var ps = m.GetParameters();
            var args = new object[ps.Length];
            try
            {
                for (var i = 0; i < ps.Length; i++)
                {
                    var declared = ps[i].ParameterType;
                    var target = declared.IsByRef ? declared.GetElementType() : declared;
                    if (hintTypes != null && hintTypes.Length > i && hintTypes[i] != null) target = hintTypes[i];
                    args[i] = Decode(argNodes[i], target);
                }
                return (m, args);
            }
            catch (Exception ex) { last = ex; }
        }

        throw new InvalidOperationException($"no overload of {type.Name}.{name} accepted the args: {last?.Message}");
    }

    private static (ConstructorInfo, object[]) BindCtor(Type type, JsonArray argNodes, JsonArray paramHints)
    {
        var n = argNodes?.Count ?? 0;
        var hintTypes = paramHints?.Select(p => ResolveType(p!.GetValue<string>())).ToArray();
        var cands = type.GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .Where(c => c.GetParameters().Length == n).ToList();
        if (cands.Count == 0) throw new InvalidOperationException($"no ctor for {type.FullName} with {n} args");

        Exception last = null;
        foreach (var c in cands)
        {
            var ps = c.GetParameters();
            var args = new object[ps.Length];
            try
            {
                for (var i = 0; i < ps.Length; i++)
                {
                    var declared = ps[i].ParameterType;
                    var target = declared.IsByRef ? declared.GetElementType() : declared;
                    if (hintTypes != null && hintTypes.Length > i && hintTypes[i] != null) target = hintTypes[i];
                    args[i] = Decode(argNodes[i], target);
                }
                return (c, args);
            }
            catch (Exception ex) { last = ex; }
        }
        throw new InvalidOperationException($"no ctor accepted the args: {last?.Message}");
    }

    private static JsonNode GetMember(JsonObject req)
    {
        var name = req["name"]!.GetValue<string>();
        object target = null;
        Type type;
        if (req.TryGetPropertyValue("handle", out var h) && h != null)
        {
            target = HandleTable.Get(h.GetValue<int>());
            type = target.GetType();
        }
        else type = ResolveType(req["type"]!.GetValue<string>());

        var ui = req["ui"]?.GetValue<bool>() ?? true;
        Func<object> get = () =>
        {
            const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
            var prop = type.GetProperty(name, flags);
            if (prop != null) return prop.GetValue(target);
            var field = type.GetField(name, flags);
            if (field != null) return field.GetValue(target);
            throw new InvalidOperationException($"no member {type.FullName}.{name}");
        };

        return Encode(ui ? UiThread.Run(get) : get());
    }

    private static JsonNode SetMember(JsonObject req)
    {
        var name = req["name"]!.GetValue<string>();
        var valueNode = req["value"];
        object target = null;
        Type type;
        if (req.TryGetPropertyValue("handle", out var h) && h != null)
        {
            target = HandleTable.Get(h.GetValue<int>());
            type = target.GetType();
        }
        else type = ResolveType(req["type"]!.GetValue<string>());

        var ui = req["ui"]?.GetValue<bool>() ?? true;
        Func<object> set = () =>
        {
            const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
            var prop = type.GetProperty(name, flags);
            if (prop != null)
            {
                prop.SetValue(target, Decode(valueNode, prop.PropertyType));
                return true;
            }
            var field = type.GetField(name, flags);
            if (field != null)
            {
                field.SetValue(target, Decode(valueNode, field.FieldType));
                return true;
            }
            throw new InvalidOperationException($"no member {type.FullName}.{name}");
        };

        return Encode(ui ? UiThread.Run(set) : set());
    }

    private static JsonNode Handles()
    {
        var arr = new JsonArray();
        foreach (var kv in HandleTable.Snapshot())
            arr.Add(new JsonObject { ["handle"] = kv.Key, ["type"] = kv.Value.GetType().FullName });
        return arr;
    }

    private static JsonNode Release(JsonObject req)
    {
        HandleTable.Release(req["handle"]!.GetValue<int>());
        return JsonValue.Create(true);
    }

    private static JsonNode Docs()
    {
        return UiThread.Run<JsonNode>(() =>
        {
            var arr = new JsonArray();
            try
            {
                var mgrType = ResolveType("M98");
                var mgr = mgrType.GetMethod("k9y", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
                    ?.Invoke(null, null);
                if (mgr == null) return arr;

                var nIad = mgr.GetType().GetMethod("nIad", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                var clients = nIad?.Invoke(mgr, null) as System.Collections.IEnumerable;
                if (clients == null) return arr;

                foreach (var c in clients)
                {
                    var o = new JsonObject { ["clientType"] = c.GetType().FullName };
                    var closed = c.GetType().GetMethod("get_IsClosed", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                    if (closed != null) o["isClosed"] = (bool)closed.Invoke(c, null);
                    var doc = c.GetType().GetMethod("XIXf", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)?.Invoke(c, null);
                    if (doc != null)
                    {
                        o["docType"] = doc.GetType().FullName;
                        var fn = doc.GetType().GetProperty("FileName", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                        if (fn != null) o["fileName"] = fn.GetValue(doc)?.ToString();
                        var name = doc.GetType().GetMethod("MNkV", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)?.Invoke(doc, null);
                        if (name != null) o["displayName"] = name.ToString();
                        o["handle"] = HandleTable.Add(c);
                        o["docHandle"] = HandleTable.Add(doc);
                    }
                    arr.Add(o);
                }
            }
            catch (Exception ex) { return new JsonArray(new JsonObject { ["error"] = ex.Message }); }
            return arr;
        });
    }

    /// <summary>Stable per-object identity so callers can tell "same live object" from "fresh copy".</summary>
    private static JsonNode Identity(JsonObject req)
    {
        var target = HandleTable.Get(req["handle"]!.GetValue<int>());
        return new JsonObject
        {
            ["type"] = target.GetType().FullName,
            ["id"] = System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(target),
            ["str"] = Truncate(target.ToString(), 120)
        };
    }

    private static JsonNode Subtypes(JsonObject req)    {
        var baseType = ResolveType(req["type"]!.GetValue<string>());
        var includeAbstract = req["abstract"]?.GetValue<bool>() ?? false;
        var limit = req["limit"]?.GetValue<int>() ?? 300;

        var arr = new JsonArray();
        foreach (var asm in Assemblies())
        {
            Type[] types;
            try { types = asm.GetTypes(); }
            catch (ReflectionTypeLoadException rtle) { types = rtle.Types.Where(t => t != null).ToArray(); }
            catch { continue; }

            foreach (var t in types)
            {
                if (t == baseType || !baseType.IsAssignableFrom(t)) continue;
                if (!includeAbstract && (t.IsAbstract || t.IsInterface)) continue;
                var o = new JsonObject
                {
                    ["type"] = t.FullName ?? t.Name,
                    ["abstract"] = t.IsAbstract
                };
                try { o["ctors"] = new JsonArray(t.GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
                    .Select(c => (JsonNode)JsonValue.Create(c.GetParameters().Length)).ToArray()); }
                catch { }
                arr.Add(o);
                if (arr.Count >= limit) return arr;
            }
        }
        return arr;
    }

    private static JsonNode Dump(JsonObject req)
    {
        var depth = req["depth"]?.GetValue<int>() ?? 3;
        var limit = req["limit"]?.GetValue<int>() ?? 50;
        var includeFields = req["fields"]?.GetValue<bool>() ?? false;

        object target;
        if (req.TryGetPropertyValue("handle", out var h) && h != null) target = HandleTable.Get(h.GetValue<int>());
        else target = ResolveType(req["type"]!.GetValue<string>());

        var seen = new HashSet<int>();
        return UiThread.Run<JsonNode>(() => DumpValue(target, depth, limit, includeFields, seen));
    }

    private static JsonNode DumpValue(object value, int depth, int limit, bool includeFields, HashSet<int> seen)
    {
        if (value == null) return null;
        var t = value.GetType();

        if (value is string || value is bool || value is Guid || value is Enum || t.IsPrimitive || value is decimal)
            return Encode(value);

        if (depth <= 0)
            return new JsonObject { ["$h"] = HandleTable.Add(value), ["type"] = t.FullName, ["str"] = Truncate(value.ToString(), 120) };

        var id = System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(value);
        if (!seen.Add(id))
            return new JsonObject { ["$ref"] = t.FullName };

        // collections
        if (value is System.Collections.IEnumerable seq && value is not System.Collections.IEnumerable && false) { }

        if (value is System.Collections.IEnumerable enumerable && value is not string)
        {
            var arr = new JsonArray();
            var i = 0;
            foreach (var item in enumerable)
            {
                if (i++ >= limit) { arr.Add("…"); break; }
                arr.Add(DumpValue(item, depth - 1, limit, includeFields, seen));
            }
            return new JsonObject
            {
                ["$h"] = HandleTable.Add(value),
                ["type"] = t.FullName,
                ["count"] = i,
                ["items"] = arr
            };
        }

        var obj = new JsonObject
        {
            ["$h"] = HandleTable.Add(value),
            ["type"] = t.FullName
        };

        const BindingFlags flags = BindingFlags.Public | BindingFlags.Instance;
        foreach (var p in t.GetProperties(flags))
        {
            if (p.GetIndexParameters().Length > 0) continue;
            object pv;
            try { pv = p.GetValue(value); }
            catch { continue; }
            obj[p.Name] = DumpValue(pv, depth - 1, limit, includeFields, seen);
        }

        if (includeFields)
        {
            foreach (var f in t.GetFields(flags))
            {
                object fv;
                try { fv = f.GetValue(value); }
                catch { continue; }
                obj["f_" + f.Name] = DumpValue(fv, depth - 1, limit, includeFields, seen);
            }
        }

        return obj;
    }

    private static JsonNode Iter(JsonObject req)
    {
        var limit = req["limit"]?.GetValue<int>() ?? 200;
        var handle = req["handle"]!.GetValue<int>();
        var target = HandleTable.Get(handle);
        return UiThread.Run<JsonNode>(() =>
        {
            if (target is not System.Collections.IEnumerable seq || target is string)
                throw new InvalidOperationException("not enumerable: " + target.GetType().FullName);
            var arr = new JsonArray();
            var i = 0;
            foreach (var item in seq)
            {
                if (i++ >= limit) break;
                arr.Add(Encode(item));
            }
            return arr;
        });
    }

    // ---------- value encoding ----------
    public static JsonNode Encode(object value)
    {
        switch (value)
        {
            case null: return null;
            case bool b: return JsonValue.Create(b);
            case string s: return JsonValue.Create(s);
            case byte[] bytes: return JsonValue.Create(Convert.ToBase64String(bytes));
            case sbyte or byte or short or ushort or int or uint or long:
                return JsonValue.Create(Convert.ToInt64(value));
            case ulong ul: return JsonValue.Create(unchecked((long)ul));
            case float or double or decimal:
                return JsonValue.Create(Convert.ToDouble(value));
            case char c: return JsonValue.Create(c.ToString());
            case Guid g: return JsonValue.Create(g.ToString());
            case Enum e: return JsonValue.Create(e.ToString());
            case Type t: return new JsonObject { ["$type"] = t.FullName ?? t.Name };
            case JsonNode node: return node.DeepClone();
            default:
                return new JsonObject
                {
                    ["$h"] = HandleTable.Add(value),
                    ["type"] = value.GetType().FullName,
                    ["str"] = Truncate(value.ToString(), 400)
                };
        }
    }

    private static string Truncate(string s, int max) =>
        s == null ? null : s.Length <= max ? s : s.Substring(0, max) + "…";

    public static object Decode(JsonNode node, Type expected)
    {
        if (node == null) return null;

        var underlying = Nullable.GetUnderlyingType(expected) ?? expected;

        switch (node)
        {
            case JsonObject obj when obj.ContainsKey("$h"):
                var handle = obj["$h"]!.GetValue<int>();
                var target = HandleTable.Get(handle);
                if (!underlying.IsInstanceOfType(target))
                    throw new InvalidOperationException($"handle {handle} ({target.GetType().Name}) is not {underlying.Name}");
                return target;

            case JsonObject obj when obj.ContainsKey("$t"):
                return ResolveType(obj["$t"]!.GetValue<string>());

            case JsonObject obj when obj.ContainsKey("$arr"):
                var list = obj["$arr"]!.AsArray();
                // generic collection target (IEnumerable<T>, IList<T>, List<T>, ...) -> build a List<T>
                if (underlying.IsGenericType)
                {
                    var def = underlying.GetGenericTypeDefinition();
                    if (def == typeof(IEnumerable<>) || def == typeof(ICollection<>) || def == typeof(IList<>) ||
                        def == typeof(List<>) || def == typeof(IReadOnlyList<>) || def == typeof(IReadOnlyCollection<>))
                    {
                        var t = underlying.GetGenericArguments()[0];
                        var built = (System.Collections.IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(t))!;
                        foreach (var item in list) built.Add(Decode(item, t));
                        return built;
                    }
                }
                var elem = underlying.IsArray ? underlying.GetElementType() : typeof(object);
                var array = Array.CreateInstance(elem!, list.Count);
                for (var i = 0; i < list.Count; i++) array.SetValue(Decode(list[i], elem), i);
                return array;

            case JsonObject obj when obj.ContainsKey("$guid"):
                return Guid.Parse(obj["$guid"]!.GetValue<string>());
        }

        if (underlying == typeof(string)) return node.GetValue<string>();
        if (underlying == typeof(byte[])) return Convert.FromBase64String(node.GetValue<string>());
        if (underlying == typeof(bool)) return node.GetValue<bool>();
        if (underlying == typeof(Guid)) return Guid.Parse(node.GetValue<string>());
        if (underlying == typeof(Type)) return ResolveType(node.GetValue<string>());
        if (underlying.IsEnum)
        {
            if (node.GetValueKind() == JsonValueKind.String)
                return Enum.Parse(underlying, node.GetValue<string>(), ignoreCase: true);
            return Enum.ToObject(underlying, node.GetValue<long>());
        }
        if (underlying == typeof(int)) return node.GetValue<int>();
        if (underlying == typeof(long)) return node.GetValue<long>();
        if (underlying == typeof(short)) return (short)node.GetValue<int>();
        if (underlying == typeof(byte)) return (byte)node.GetValue<int>();
        if (underlying == typeof(uint)) return (uint)node.GetValue<long>();
        if (underlying == typeof(double)) return node.GetValue<double>();
        if (underlying == typeof(float)) return (float)node.GetValue<double>();
        if (underlying == typeof(decimal)) return node.GetValue<decimal>();

        if (underlying == typeof(object))
        {
            return node.GetValueKind() switch
            {
                JsonValueKind.String => node.GetValue<string>(),
                JsonValueKind.True or JsonValueKind.False => node.GetValue<bool>(),
                JsonValueKind.Number => node.GetValue<double>(),
                JsonValueKind.Object => Decode(node, typeof(object)),
                _ => null
            };
        }

        throw new InvalidOperationException($"cannot convert JSON to {underlying.FullName}");
    }
}

internal static class HandleTable
{
    private static int _next = 1;
    private static readonly ConcurrentDictionary<int, object> Table = new();

    public static int Add(object o)
    {
        if (o == null) return 0;
        var id = Interlocked.Increment(ref _next);
        Table[id] = o;
        return id;
    }

    public static object Get(int id) =>
        Table.TryGetValue(id, out var o) ? o : throw new InvalidOperationException("unknown handle " + id);

    public static void Release(int id) => Table.TryRemove(id, out _);

    public static IEnumerable<KeyValuePair<int, object>> Snapshot() => Table.ToArray();
}




