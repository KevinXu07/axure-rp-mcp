# macOS adapter and verification

## Scope

The Mac build is a different application model from Windows. This adapter is pinned to **Axure RP 11.0.0.4137, Client.Mac, .NET 8.0.11, Apple Silicon**. The Windows adapter targets the upstream Windows build and .NET 10. A bridge `ping` is not proof that the Windows document methods work on Mac.

The Mac adapter was exercised through newline-delimited JSON-RPC over the real stdio MCP server, against a disposable copy of `test/clean.rp`. Its supported tools and 16 widget classes were checked after a real Axure process exit and relaunch. Other Mac versions, Intel Macs, and interactive Windows Axure behavior have not been tested in this audit. CI checks the portable Python layer and builds/tests the bridge; it does not install or drive Axure.

## Build and launch

Install the official .NET 10 SDK and Python 3.10 or newer. The SDK can build both bridge targets; Mac Axure supplies its own .NET 8 runtime.

```sh
dotnet build bridge/AxureBridge.csproj -c Debug -f net8.0
python3 -m axure_mcp.launcher /absolute/path/to/disposable-test.rp
```

First save and quit an Axure instance that was started without the hook. The launcher reuses an existing bridge, so its optional filename is only used when starting a new Axure process; use `axure_open_file` to change documents in a running hooked instance.

The default executable is `/Applications/Axure RP 11.app/Contents/MacOS/Axure RP 11`. `AXURE_EXE` accepts that executable or the `.app` directory. `AXURE_BRIDGE_DLL` overrides assembly selection. Mac automatically selects `bridge/bin/Debug/net8.0/AxureBridge.dll` or `bridge/dist/net8.0/AxureBridge.dll`, never a Windows net10 binary. Existing `DOTNET_STARTUP_HOOKS` entries are preserved.

MCP client configuration (substitute absolute paths):

```json
{
  "mcpServers": {
    "axure": {
      "command": "/absolute/path/to/python3",
      "args": ["-m", "axure_mcp.server"],
      "cwd": "/absolute/path/to/axure-rp-mcp",
      "env": { "AXURE_AUTO_LAUNCH": "1" }
    }
  }
}
```

Startup is lazy: `initialize`, `ping`, `tools/list`, and invalid argument checks do not launch Axure or wait for its GUI. The first valid document tool connects/launches it. `axure_status` reports the bridge runtime and supported tool names. Rebuild the bridge if an older assembly does not report `appVersion`.

Discovery, session state, and operation locks use `~/Library/Application Support/Axure`, matching .NET's `LocalApplicationData`. No installed Axure bundle files or signatures are changed.

## Capability matrix

On macOS `tools/list` advertises these **19** tools. Direct calls to the other **18** registered tools fail before document access with a capability error. They retain their existing Windows implementation.

| Mac capability | Tools | Verification |
| --- | --- | --- |
| Status and reads | status, list_pages, page_tree, get_page | Real MCP responses; document/page identity checked |
| Pages | create_page, rename_page, move_page, delete_page | Creation/reordering/deletion exercised; created page and deleted temporary page checked after restart |
| Widgets | add_widget, update_widget, delete_widget | 16 types inserted, text/geometry/visibility read back after restart; deletion exercised |
| Style | style_widget, get_widget_style | Fresh fill objects, text/border colours, font, weight, italic, underline, opacity, radius, alignment, spacing, padding, rotation |
| Geometry | align, distribute, same_size | Resulting coordinates and sizes persisted and compared after restart |
| Files | save, save_as, open_file | Explicit page serialization, file write, byte-identical copy, MCP reopen, then separate process restart |

Supported creation kinds: button, checkbox, radiobutton, textbox, textarea, droplist, listbox, dynamicpanel, image, inlineframe, table, repeater, group, hotspot, snapshot, shape.

- `connector` and `textlink` insertion are rejected before model access. Their normal editor construction needs additional initialization.
- Only button/checkbox/radiobutton/textbox/textarea/droplist/listbox/image/shape accept the generic `text` argument on creation. Omit it for containers and non-text widgets.
- The native model normalizes some requested dimensions: checkbox/radio height, repeater bounds, and empty group bounds. The returned geometry is authoritative.
- **Mac insertion uses the model collection and does not create a native undo entry.** `axure_undo` is not advertised on Mac. Work on a saved copy.
- Folder creation/deletion/movement, interactions, grouping/ungrouping, z-order, list option replacement, image embedding, table cell editing, rich text, panel states, notes, undo, and native PNG rendering are not implemented by this Mac adapter. Creating an image/table/panel widget does not imply those content operations work.
- `save_as` retains upstream semantics: save the current file and copy it; the editor continues working on the source. It is not a native Save As dialog.
- Native screenshot inspection is separate from the bridge's render tool. The absence of Mac rendering support is not replaced by a desktop screenshot pretending to be a render result.

## Bugs reproduced and fixed

1. Windows-only executable selection on Mac: default launch failed with a `D:\\...AxureRP11.exe` missing-file error.
2. The net10 bridge could not target Mac's net8 host. The project now multi-targets net8/net10 and selects the matching binary.
3. Python looked for `~/Axure/axure-bridge.json`; C# wrote into `~/Library/Application Support/Axure`.
4. Windows `tasklist` liveness checking did not work on POSIX. Mac now checks the PID with `kill(pid, 0)` and rejects nonpositive PIDs.
5. Document calls loaded `user32.dll` on Mac. Paint/dialog guards now only call Win32 on Windows.
6. The Mac host disables default JSON reflection metadata. `types` failed with the .NET `TypeInfoResolver` error. The bridge explicitly provides the default resolver.
7. Windows document discovery (`M98`) and the built-in MCP `DocumentToolService` do not exist in this Mac build. Mac discovery uses Cocoa documents and a separate model adapter.
8. Missing UI dispatch used to fall back to a worker thread. It now fails closed. Mac uses `NSApplication.BeginInvoke`; asynchronous null/void operations signal completion independently of their return value. A queued operation is cancelled when its dispatch deadline expires; a running operation reports an uncertain outcome and must be inspected before retry.
9. Calling the native Mac `Save` alone produced a success response but lost inserted widgets on reload. The adapter serializes the active package with the host's writer before saving the container.
10. A timeout during dialog recovery recursively opened more dialog-recovery connections. Recovery requests and probes no longer recover recursively; failed sockets and partial buffers are discarded, and successful response IDs are checked.
11. The process-wide nesting counter allowed a second thread to enter the bridge critical section. A re-entrant thread lock plus thread-local nesting now encloses the OS file lock. Windows always locks/unlocks byte zero of a nonempty lock file.
12. Malformed JSON-RPC envelopes could terminate the server. Null arguments, array element type errors, and nonfinite numbers could pass validation. Validation is recursive and request errors leave the server alive. Notifications never receive replies, unsupported batch requests are rejected as invalid requests, and stdio is explicitly UTF-8 even under a legacy Windows code page.
13. Cocoa document discovery records the active document, and the Mac client is resolved for the current window rather than kept from an earlier window.
14. Direct collection mutations persisted but were absent from the live canvas until reload. Saving now reconstructs the editor page after serialization, then invalidates/displays the Cocoa view tree on the UI thread. Previously returned widget handles are rebound by persistent GUID within the requested page, so updates after refresh remain valid and cross-page handles are rejected.
15. Unnamed widgets were inserted but omitted from `get_page`. Page reads now include unnamed widgets so their handles can be used for editing/deletion.

## Reproduce the checks

Portable checks (no Axure required):

```sh
python3 -m unittest discover -s tests -v
dotnet build bridge/AxureBridge.csproj
dotnet run --project tests/bridge/BridgeTests.csproj
```

Live Mac check:

1. Copy `test/clean.rp` to a new disposable absolute path and open it in the hooked Axure instance.
2. Run the exercise. The script refuses to modify a different current document, overwrite its report, or overwrite its export copy.

   ```sh
   python3 tools/regression_macos.py --exercise /absolute/path/mac-test.rp --report /absolute/path/mac-report.json
   ```

3. Save/quit Axure and relaunch the hooked app with the same `.rp`. Run:

   ```sh
   python3 tools/regression_macos.py --verify /absolute/path/mac-report.json
   ```

The report records runtime, original and new PIDs, tool calls, widget state, style state, and export path. `--verify` rejects the same PID and compares saved state after restart. Keep local paths and machine-specific reports out of public commits unless reviewed/redacted.
