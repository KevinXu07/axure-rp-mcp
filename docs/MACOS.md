# macOS adapter and verification

## Scope

The macOS build uses a different document model from Windows. This adapter is pinned to
**Axure RP 11.0.0.4137, Client.Mac, .NET 8.0.11** and has been tested on **Apple Silicon**.
Other Axure builds fail closed before document access.

The current adapter exposes **35 of the 37 registered tools** and supports creation of
**16 widget classes**. `axure_render_page` and `axure_undo` remain unavailable because the
verified Mac model does not provide equivalent safe operations. `connector` and `textlink`
insertion are also rejected.

The supported surface was exercised through the real newline-delimited stdio MCP server against
disposable copies of `test/clean.rp`. Advanced content, notes, grouping, folders and interactions
were saved, read back, compared after a real Axure process exit/relaunch, and inspected on the
native editor canvas. Axure's **Publish > Preview** output was also opened and its table, selected
list item, group member, image, rich text and interactions were visible in the rendered prototype.
Windows behavior was not retested in this audit, and no Intel Mac was available.

## Build and launch

Install the official .NET 10 SDK and Python 3.10 or newer. The SDK cross-targets the bridge;
Mac Axure supplies its own .NET 8 runtime.

```sh
dotnet build bridge/AxureBridge.csproj -c Debug -f net8.0
python3 -m axure_mcp.launcher /absolute/path/to/disposable-test.rp
```

First save and quit an Axure instance started without the hook. The launcher reuses an existing
bridge, validates that an optional filename is an existing `.rp` file, and starts Axure in a new
POSIX session on macOS. Closing the terminal or agent that launched it does not deliver a terminal
hangup to the GUI. The optional filename is used only when the launcher starts Axure.
`axure_open_file` opens the
requested `.rp` in a separate Axure document window through Cocoa's shared `NSDocumentController`.
The previous window remains open. Subsequent MCP calls follow the key or main document window; when
Cocoa temporarily reports no active window while Axure is inactive, they remain bound to the
document opened or selected most recently.

The default executable is `/Applications/Axure RP 11.app/Contents/MacOS/Axure RP 11`.
`AXURE_EXE` accepts either that executable or the `.app` directory. `AXURE_BRIDGE_DLL` overrides
assembly selection. The Mac launcher selects the net8 Debug or dist assembly and never a Windows
net10 binary. Existing `DOTNET_STARTUP_HOOKS` entries are preserved.

MCP client configuration:

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

Startup is lazy: `initialize`, `ping`, `tools/list` and invalid-argument checks do not launch
Axure. The first valid document tool connects to or launches it. `axure_status` reports the bridge
runtime, active file and supported tool names. Rebuild the bridge if an older assembly does not
report `appVersion`.

Discovery, session state and operation locks use `~/Library/Application Support/Axure`, matching
.NET's `LocalApplicationData`. Installed Axure bundle files and signatures are not modified.

## Capability matrix

On macOS, `tools/list` advertises exactly these 35 tools:

| Area | Tools |
| --- | --- |
| Status and reads | `axure_status`, `axure_list_pages`, `axure_page_tree`, `axure_get_page` |
| Pages and folders | `axure_create_page`, `axure_create_folder`, `axure_rename_page`, `axure_delete_page`, `axure_move_page` |
| Widgets | `axure_add_widget`, `axure_update_widget`, `axure_delete_widget` |
| Style | `axure_style_widget`, `axure_get_widget_style` |
| Geometry | `axure_align`, `axure_distribute`, `axure_same_size` |
| Content | `axure_set_list_items`, `axure_set_image`, `axure_set_table`, `axure_set_rich_text`, `axure_panel_states`, `axure_add_panel_state` |
| Notes | `axure_set_page_note`, `axure_get_page_notes`, `axure_set_widget_note`, `axure_get_widget_notes` |
| Grouping and order | `axure_group`, `axure_ungroup`, `axure_z_order` |
| Interactions | `axure_add_interaction`, `axure_get_interactions` |
| Files | `axure_save`, `axure_save_as`, `axure_open_file` |

The two registered tools omitted from Mac `tools/list` are:

- `axure_render_page`: no equivalent native server-side render entrypoint was verified.
- `axure_undo`: direct model insertion persists but does not create the editor undo entry that
  the Windows adapter relies on.

`axure_get_page` reports geometry and common text. On Mac, `deep=true` also returns group members.
List, image, table, group and panel records include content readbacks. Image records include byte
length and SHA-256.

### Pages, widgets and folders

Supported widget creation kinds are:

`button`, `checkbox`, `radiobutton`, `textbox`, `textarea`, `droplist`, `listbox`,
`dynamicpanel`, `image`, `inlineframe`, `table`, `repeater`, `group`, `hotspot`,
`snapshot`, `shape`.

Folders support create, nested create, rename, move and recursive delete. Deleting a subtree that
would remove every page is refused. Moving a node into its own descendants is refused before a
write.

The native model normalizes some requested dimensions: checkbox/radio height, repeater bounds and
empty group bounds. Treat returned geometry as authoritative.

### Lists, images, tables and rich text

- `axure_set_list_items` replaces a droplist/listbox list atomically and supports an empty list.
  A selected index outside the supplied items is rejected before model access.
- `axure_set_image` embeds and replaces image bytes. Mac's image constructor takes a content/cache
  key; treating that argument as a file format caused same-size images to alias. Mac now passes
  `None`, reads the stored bytes back and returns SHA-256. Malformed or zero-byte base64 is rejected
  before connecting to Axure.
- `axure_set_table` validates dimensions, cell bounds, row heights and column widths before writes.
  It resizes and rebuilds Mac's cell collection before writing text, rolls dimensions back if the
  rebuilt collection is incomplete, supports up to 10,000 cells, and returns full readbacks.
- `axure_set_rich_text` supports multi-paragraph content. Shapes use rich text; plain text widgets
  receive the newline form.
- `axure_add_panel_state` duplicates existing states, can rename the final new state, and verifies
  the resulting count and name.

### Notes

Mac page notes and widget notes use different field sets. The adapter clones Axure's shared default
attributed string before editing it; mutating the default directly caused page and widget notes to
overwrite one another. The advanced restart test keeps one page note and two independent widget
notes in the same page.

### Groups and z-order

Axure's Mac group (`Layer`) stores membership while members remain in the page's global object
collection. The adapter keeps members in that collection, adds membership to the layer and uses the
persistent widget identity to avoid duplicates. A failed grouping removes membership and the
temporary layer without re-adding widgets to the page.

`axure_group` requires at least two unique top-level widgets. Nested group members are visible with
`axure_get_page(deep=true)` and remain editable by persistent GUID. `axure_ungroup` removes
membership and the layer.

`axure_z_order` supports only `front` and `back`, for unique top-level widgets. The public Mac schema
rejects every other action.

### Interactions

The seven actions that survive a Mac save/reload audit are:

- `navigate`: existing page id; `Current`, `New`, `Popup` or `Parent`
- `close`
- `wait`: nonnegative milliseconds
- `set_opacity`: a finite value from 0 to 1
- `move`: Location/To or Delta/By; the value is applied to both X and Y
- `rotate`: degrees with Location/To or Delta/By
- `resize`: a positive scale factor

The batch form accepts up to 100 actions in one case. Every action is type-checked and constructed
before event/case insertion. A failed insertion removes the partial case and newly-created event.
Multiple calls on one event create independent cases.

The Mac action path must be built with
`hb8r.nb8K(package, None, None, {"$guid": target_guid})`. The two-argument path constructor is for
deserialization and can resolve the default target to the current widget.

## Current limitations

- Only Axure RP 11.0.0.4137 on Apple Silicon with .NET 8.0.11 is verified. Unknown versions fail
  closed instead of probing Windows-only methods.
- Native Preview works, but `axure_render_page` is not advertised or emulated with a desktop
  screenshot.
- `axure_undo` is unavailable. Direct model insertion does not create a native undo entry. Use a
  disposable copy and keep source control or Axure backups.
- `connector` and `textlink` creation are rejected. Their normal editor construction needs extra
  initialization that has not been solved safely.
- `axure_z_order` supports top-level `front`/`back`, not forward/backward or arbitrary indices.
- `axure_save_as` retains upstream semantics: save the current file and copy it. The editor remains
  on the source file; this is not an interactive native Save As dialog.
- `axure_get_widget_notes` returns notes for nested group members as well as top-level widgets.
  It reports owner id and field values; that owner id is not itself a stable MCP widget handle.
- Concurrent sessions are serialized. Multiple document windows are supported: active-window
  selection takes priority. The bridge checks the key window before the controller's
  `currentDocument`, because that controller value can remain stale while another document is
  focused. For an inactive app, it uses the current document, then main and ordered windows. The
  Python client retains the most recently opened or selected native document identity while no
  active window is available.

## Bugs reproduced and fixed

1. Windows executable selection failed on macOS; the launcher now selects the Mac `.app`/executable.
2. A net10 bridge could not load in Mac's net8 host; the bridge now multi-targets net8/net10.
3. Python and C# disagreed on discovery location; both now use `~/Library/Application Support/Axure`.
4. Windows `tasklist` liveness checks failed on POSIX; Mac now uses `kill(pid, 0)`.
5. Document calls loaded `user32.dll`; Win32 paint/dialog code is now platform-gated.
6. Mac disables default JSON reflection metadata; the bridge supplies a resolver explicitly.
7. Windows document discovery methods are absent in the Mac build; Cocoa discovery and a Mac model
   adapter now supply document/active-window identity.
8. Missing UI dispatch used to fall back to a worker thread. It now fails closed. Mac marshals to
   `NSApplication.BeginInvoke` and distinguishes queued cancellation from an uncertain running call.
9. Native Mac `Save` alone lost inserted widgets on reload. The adapter now serializes the active
   package before saving the container.
10. Timeout recovery recursively opened dialog-recovery connections. Recovery and probe requests
    no longer recurse, and stale sockets/buffers are discarded.
11. A process-wide nesting counter admitted another thread into the critical section. The lock is
    now re-entrant per thread and encloses the OS file lock.
12. Malformed JSON-RPC envelopes, nulls, wrong array types and nonfinite numbers could terminate or
    bypass validation. Validation is recursive, notifications never receive replies and stdio is
    explicitly UTF-8.
13. Cocoa document discovery attached to the wrong window; the client is resolved for the active
    document window.
14. Direct mutations were invisible until reload. Save now serializes, reconstructs the editor page
    and invalidates/displays the Cocoa view tree on the UI thread.
15. Refreshing the page invalidated old bridge handles. Handles now rebind by persistent GUID only
    inside the requested page, so cross-page handles are rejected.
16. Unnamed widgets were omitted from page reads. They now appear with editable handles.
17. Mac image assignment treated a cache key as a format and aliased same-size replacements. Passing
    `None` plus SHA-256 readback fixes replacement correctness.
18. Page and widget notes aliased a shared default rich string. Cloning before editing makes the
    notes independent and persistent.
19. Group members are global page objects with a layer overlay. Removing them from the global
    collection broke ancestry and notes; the adapter now manages membership without duplicating or
    detaching them.
20. Interaction target paths built through the deserialization constructor could target the wrong
    widget. The explicit GUID path constructor fixes target resolution; opacity, move, rotate and
    resize descriptions are now checked after reload.
21. The Mac folder payload, list generic type, table dimensions and panel-state edit sequencing are
    Mac-specific. They are now implemented and tested instead of falling through to Windows names.
22. An ImageBox with no asset throws `NullReferenceException` when its length is queried. Empty image
    readback now reports zero bytes, and any other unsupported content readback degrades to a
    per-widget `content_error` instead of making the whole page unreadable.
23. macOS `OpenDocumentInSameAXDocument` updated the model canvas but left the Pages panel bound to
    the old Cocoa document. `axure_open_file` now opens an independent document window through
    `NSDocumentController.OpenDocument`, waits for Cocoa registration and records its stable native
    identity.
24. Cocoa returns `nil` as an object wrapper rather than a CLR null, and document discovery relied
    on a fragile class-context lookup for `currentDocument`. The bridge now normalizes Cocoa nil
    objects, enumerates `NSDocument` objects directly, and falls back to key, main, and ordered
    window lookup when the controller has no current document.
25. Launching Axure directly from a PTY left it in the terminal session; closing that terminal or
    agent could hang up the GUI after a successful bridge handshake. POSIX launches now use a new
    session, with a live test confirming Axure survives the launcher process.
26. Cocoa's `currentDocument` could lag behind the actual focused window, and ordinary bridge
    handles are allocated per response. The bridge now prioritizes the key window, exposes each
    document's native identity, and lets the adapter select a fresh handle from the focused window.
27. A freshly inserted Mac table starts as 1×1. The table editor resolved its cells before resizing,
    so a valid larger table could be rejected even though setting `Rows`/`Columns` rebuilds the
    cell collection. The adapter now resizes first, verifies the rebuilt collection, and restores
    the original dimensions on failure.
28. A bridge can start listening before Cocoa finishes constructing Axure's application object.
    Immediately quitting and relaunching could therefore report a false success and then lose the
    process with `NSApplication` → `MXApp` conversion failure. The launcher now requires a stable
    bridge/process pair and retries once when Axure exits during Cocoa startup.
29. Mac batch interaction actions were only checked as generic objects at the MCP boundary, so an
    otherwise unknown nested field could be ignored when its value was `null`. The public Mac schema
    now validates every action object and rejects unknown fields before document access.

## Reproduce the checks

Portable checks do not require Axure:

```sh
python3 -m unittest discover -s tests -v
dotnet build bridge/AxureBridge.csproj -f net8.0
dotnet run --project tests/bridge/BridgeTests.csproj
```

Live Mac checks use disposable absolute paths:

1. Build and launch the hooked app with a copy of `test/clean.rp`.
2. Run the baseline and advanced exercises:

   ```sh
   python3 tools/regression_macos.py \
     --exercise /absolute/path/mac-basic.rp \
     --report /absolute/path/mac-basic.json

   python3 tools/regression_macos_advanced.py \
     --exercise /absolute/path/mac-advanced.rp \
     --report /absolute/path/mac-advanced.json
   ```

3. Quit Axure gracefully and relaunch the hooked app with each exact `.rp`. Verify:

   ```sh
   python3 tools/regression_macos.py --verify /absolute/path/mac-basic.json
   python3 tools/regression_macos_advanced.py --verify /absolute/path/mac-advanced.json
   ```

The advanced report records 85 MCP calls, expected validation failures, 14 widgets, independent
notes, image/table/list content, grouping, folder hierarchy and interaction cases. Verification
rejects the original PID and compares the complete saved state after relaunch.

CI runs Python tests on supported interpreters and builds/tests the C# bridge. CI cannot install or
drive the proprietary Axure application, so native-canvas, Preview and restart evidence remains a
live-Mac check.
