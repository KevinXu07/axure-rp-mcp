"""Axure RP 11 MCP server: read *and write* the live document.

Protocol: MCP over stdio (newline-delimited JSON-RPC 2.0), standard library only.

Run:
    python -m axure_mcp.server

Register with an MCP client (Claude Desktop / Claude Code / Cursor / ...):
    { "mcpServers": { "axure": { "command": "python",
                                 "args": ["-m", "axure_mcp.server"],
                                 "cwd": "E:\\\\code\\\\axure-mcp" } } }
"""
from __future__ import annotations

import base64
import json
import os
import sys
import traceback
import math
import copy

from .client import Bridge, BridgeError, discover
from .lock import bridge_lock
from .ops import WIDGET_TYPES, Axure, AxureError

SERVER_NAME = "axure-rp-11"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

# ---------------------------------------------------------------- tool schemas

TOOLS = [
    {
        "name": "axure_status",
        "description": "Check that Axure RP is running with the bridge and report the open document.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "axure_list_pages",
        "description": "List the pages of the open Axure document (id, name, type).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "axure_get_page",
        "description": "List the widgets on a page with their geometry, text and state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Page id from axure_list_pages; defaults to the open page."},
                "page_name": {"type": "string", "description": "Page name, used when page_id is omitted."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_add_widget",
        "description": ("Create a widget on a page and persist it. kind is one of "
                        + ", ".join(sorted(WIDGET_TYPES)) + "."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": sorted(WIDGET_TYPES)},
                "name": {"type": "string", "description": "Widget name, e.g. 'submitButton'."},
                "text": {"type": "string", "description": "Label / content text."},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "width": {"type": "number"},
                "height": {"type": "number"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_update_widget",
        "description": "Change a widget's text, geometry, visibility or name, then persist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Existing widget name."},
                "widget_id": {"type": "integer", "description": "Widget handle from axure_get_page."},
                "text": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "width": {"type": "number"},
                "height": {"type": "number"},
                "visible": {"type": "boolean"},
                "new_name": {"type": "string"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_delete_widget",
        "description": "Delete a widget from a page, then persist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_render_page",
        "description": "Render a page to PNG with Axure's own renderer and return the image.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
                "max_dimension": {"type": "integer", "description": "Longest edge in pixels, default 1400."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_save",
        "description": "Write pending edits of the open page into the .rp file.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

PAGE_TOOLS = [
    {
        "name": "axure_page_tree",
        "description": "The sitemap as a nested tree (folders, pages, ids) - what the Pages panel shows.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "axure_create_page",
        "description": "Create a page (optionally inside a folder) and persist it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "parent_id": {"type": "string", "description": "Folder id to nest under; omit for top level."},
                "index": {"type": "integer", "description": "Position among siblings, default 0."},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_create_folder",
        "description": "Create a folder in the sitemap.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "parent_id": {"type": "string"},
                "index": {"type": "integer"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_rename_page",
        "description": "Rename a page or folder.",
        "inputSchema": {
            "type": "object",
            "properties": {"page_id": {"type": "string"}, "name": {"type": "string"}},
            "required": ["page_id", "name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_delete_page",
        "description": "Delete a page or folder from the sitemap.",
        "inputSchema": {
            "type": "object",
            "properties": {"page_id": {"type": "string"}},
            "required": ["page_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_move_page",
        "description": "Move a page/folder (into a folder or back to the top level).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "parent_id": {"type": "string", "description": "Omit to move to the top level."},
                "index": {"type": "integer"},
            },
            "required": ["page_id"],
            "additionalProperties": False,
        },
    },
]

STYLE_KEYS = {
    "fill": {"type": "string", "description": "#RRGGBB background colour."},
    "text_color": {"type": "string"},
    "border_color": {"type": "string"},
    "border_width": {"type": "number"},
    "corner_radius": {"type": "number"},
    "opacity": {"type": "number"},
    "font": {"type": "string"},
    "font_size": {"type": "number"},
    "bold": {"type": "boolean"},
    "italic": {"type": "boolean"},
    "underline": {"type": "boolean"},
    "align": {"type": "string", "enum": ["left", "center", "right", "justify"]},
    "valign": {"type": "string", "enum": ["top", "middle", "bottom"]},
    "line_spacing": {"type": "number"},
    "letter_spacing": {"type": "number"},
    "padding": {"type": "array", "items": {"type": "number"},
                "description": "[left, top, right, bottom] or a single number."},
    "rotation": {"type": "number"},
}

TOOLS += PAGE_TOOLS + [
    {
        "name": "axure_style_widget",
        "description": ("Restyle a widget (fill, border, corner radius, font, size, weight, colour, "
                        "alignment, padding, opacity) and persist."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                **STYLE_KEYS,
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_get_widget_style",
        "description": "Read a widget's effective style (font, colours, radius, border, alignment).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_z_order",
        "description": "Change stacking order of widgets: front, back, forward, backward.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}},
                "action": {"type": "string", "enum": ["front", "back", "forward", "backward"]},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["names"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_add_interaction",
        "description": ("Attach an interaction: widget --event--> case --action--> target. "
                        "Currently supports action=navigate (open another page)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "event": {"type": "string",
                          "description": "OnClick, OnMouseOver, OnLoad, OnShow, OnHide, ..."},
                "action": {"type": "string",
                           "enum": ["navigate", "close", "wait", "set_opacity", "move", "rotate",
                                    "resize"],
                           "description": ("Only these seven survive a save/reload audit. Nine other "
                                           "actions (show_hide, scroll_to, focus, set_image, apply_style, "
                                           "enable_disable, link_frame, panel_order, expand_collapse, "
                                           "set_panel_state) are rejected: Axure drops them on save or "
                                           "the file stops loading.")},
                "wait_ms": {"type": "integer", "description": "Milliseconds, for action=wait."},
                "target_widget": {"type": "string", "description": "Widget name, for scroll_to/focus/show_hide."},
                "fade": {"type": "string", "enum": ["Show", "Hide", "Toggle"],
                         "description": "For action=show_hide."},
                "value": {"type": "number",
                          "description": ("Number for set_opacity (0-1) / move / rotate (deg) / resize (factor); "
                                          "1/0 for panel_order and expand_collapse.")},
                "mode": {"type": "string", "enum": ["Enable", "Disable", "Toggle"],
                         "description": "For action=enable_disable."},
                "target_page_id": {"type": "string", "description": "Page id to open."},
                "link_type": {"type": "string", "enum": ["Current", "New", "Popup", "Parent"]},
                "actions": {"type": "array", "items": {"type": "object"},
                            "description": ("Batch form: several action specs for the same event "
                                            "(keeps Axure's tree reloadable).")},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["event"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_get_interactions",
        "description": "List a widget's events, cases and actions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_align",
        "description": "Align widgets: left, hcenter, right, top, vcenter, bottom.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string",
                         "enum": ["left", "hcenter", "right", "top", "vcenter", "bottom"]},
                "reference": {"type": "string", "enum": ["bounds", "selection"],
                              "description": "bounds = align to the group's box, selection = to the artboard."},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["names", "mode"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_distribute",
        "description": "Spread widgets evenly along an axis (equal gaps).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}},
                "axis": {"type": "string", "enum": ["horizontal", "vertical"]},
                "gap": {"type": "number", "description": "Fixed gap in px; omit for even distribution."},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["names"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_set_list_items",
        "description": ("Replace a droplist/listbox option list (labels + which one is selected). "
                        "Falls back to clearing the existing list if the Options setter refuses."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "items": {"type": "array", "items": {"type": "string"}},
                "selected": {"type": "integer"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_group",
        "description": ("Group widgets into a Layer (Axure's group object) covering their bounding box. "
                        "Ungrouping is not exposed yet - it still destabilises the document."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}},
                "group_name": {"type": "string"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["names"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_ungroup",
        "description": "Dissolve a group: members go back to the page at their original positions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_save_as",
        "description": ("Save the document under another path (saves in place, then copies the .rp). "
                        "Axure's own Save-As command opens a modal dialog, which would block the bridge."),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_set_page_note",
        "description": "Write a page note (the Notes panel field, e.g. \"Page overview\").",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "field": {"type": "string", "description": "Note field name; defaults to the first one."},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_get_page_notes",
        "description": "Read all page notes for the page.",
        "inputSchema": {
            "type": "object",
            "properties": {"page_id": {"type": "string"}, "page_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_set_widget_note",
        "description": ("Attach a note to a widget. Refused when the page already has a page note: Axure "
                        "shares annotation storage, so keeping both would overwrite the page note after a reload."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "text": {"type": "string"},
                "field": {"type": "string"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_get_widget_notes",
        "description": "List widget notes on the page (owner id, kind, field values).",
        "inputSchema": {
            "type": "object",
            "properties": {"page_id": {"type": "string"}, "page_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_set_image",
        "description": ("Embed an image into an ImageBox (base64 PNG/JPEG). Note: Axure only accepts the "
                        "first Image assignment for a widget - re-creating the widget is required to change it."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "image_base64": {"type": "string", "description": "Image bytes, base64 encoded."},
                "filename": {"type": "string"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["image_base64"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_set_table",
        "description": "Resize a table and fill its cells (row-major 2D array of strings).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "rows": {"type": "integer"},
                "columns": {"type": "integer"},
                "data": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "row_heights": {"type": "array", "items": {"type": "number"}},
                "column_widths": {"type": "array", "items": {"type": "number"}},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_set_rich_text",
        "description": ("Write multi-paragraph text into a widget. Shapes/headings/paragraphs get rich text "
                        "(RichTextPanel); plain text widgets fall back to a newline string."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "text": {"type": "string"},
                "paragraphs": {"type": "array", "items": {"type": "string"}},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_same_size",
        "description": "Give widgets the same width/height as the first one (mode: width, height, both).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "enum": ["width", "height", "both"]},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "required": ["names"],
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_panel_states",
        "description": "List the states of a dynamic panel (count + names).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_add_panel_state",
        "description": "Append state(s) to a dynamic panel (duplicates an existing state, then optionally renames it).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "widget_id": {"type": "integer"},
                "count": {"type": "integer", "description": "How many states to add, default 1."},
                "state_name": {"type": "string", "description": "Rename the new state to this."},
                "page_id": {"type": "string"},
                "page_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "axure_undo",
        "description": ("Undo the last edit (same command as Ctrl+Z). Widgets created by this server "
                        "go through Axure's own insert pipeline, so they are undoable."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "axure_open_file",
        "description": "Load an .rp file into the running Axure (HNR1.LNRH.Load).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]


# ---------------------------------------------------------------- helpers

def _text(value: str) -> dict:
    return {"type": "text", "text": value}


def _json(value) -> dict:
    return _text(json.dumps(value, ensure_ascii=False, indent=1))


_CONNECTION: dict = {"app": None}


def _connect_app() -> Axure:
    bridge = Bridge().connect()
    try:
        info = bridge.ping()
        adapter = Axure
        if info.get("entry") == "Client.Mac":
            if info.get("appVersion") != "11.0.0.4137":
                raise AxureError("unsupported Mac Axure version; adapter verified only on 11.0.0.4137; rebuild the bridge if appVersion is missing")
            from .macos import MacAxure
            adapter = MacAxure
        return adapter(bridge=bridge)
    except Exception:
        bridge.close()
        raise


def _app() -> Axure:
    """Reuse one bridge connection across tool calls.

    Every call used to build a fresh Axure() - a new socket plus a discovery read. Reusing it is
    noticeably faster, and a cheap ping on the way in means a dead/restarted Axure is detected here
    instead of surfacing as a confusing error halfway through a tool.
    """
    app = _CONNECTION.get("app")
    if app is not None:
        try:
            app.b.ping()
            return app
        except Exception:  # noqa: BLE001 - stale connection, drop it and reconnect
            try:
                app.close()
            except Exception:  # noqa: BLE001
                pass
            _CONNECTION["app"] = None
    try:
        app = _connect_app()
    except Exception as exc:  # noqa: BLE001 - bridge missing: try to bring Axure up once
        if os.environ.get("AXURE_AUTO_LAUNCH", "1") == "0" or not _looks_like_bridge_down(exc):
            raise
        from .launcher import launch
        from .state import last_file
        reopen = last_file()
        print(f"[axure-mcp] bridge down ({type(exc).__name__}); relaunching Axure"
              + (f" with {reopen}" if reopen else ""), file=sys.stderr)
        try:
            launch(open_file=reopen)
        except Exception as launch_exc:  # noqa: BLE001 - report the original problem
            raise launch_exc from exc
        app = _connect_app()
        # Do NOT call open_file() here: loading a document while the fresh window is still
        # initialising blocks until the bridge times out (observed: 190s). The remembered path is
        # exposed by axure_status instead, so the caller can reopen it explicitly.
    _CONNECTION["app"] = app
    try:  # remember the document so a relaunch can reopen it
        from .state import save as _save_state
        _save_state(file=app.file_name())
    except Exception:  # noqa: BLE001 - state is best effort
        pass
    return app


def _looks_like_bridge_down(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(needle in text for needle in
               ("connection refused", "connectionrefused", "connection reset", "timed out", "timeout",
                "bridge closed the connection", "broken pipe"))


def _reset_connection() -> None:
    app = _CONNECTION.pop("app", None)
    if app is not None:
        try:
            app.close()
        except Exception:  # noqa: BLE001
            pass


_HINTS = (
    ("Connection refused", "Axure 没在运行，或桥没有注入。用 tools/axure-hook.ps1 启动 Axure。"),
    ("ConnectionRefused", "Axure 没在运行，或桥没有注入。用 tools/axure-hook.ps1 启动 Axure。"),
    ("Connection reset", "Axure 进程可能已经崩溃。重启 Axure 后重试；必要时先保存。"),
    ("timed out", "操作超时：Axure 可能弹了模态框或正忙。检查窗口是否需要人工点击。"),
    ("no document is open", "Axure 里没有打开任何 .rp，先用 axure_open_file 打开一个。"),
    ("bridge", "桥不可用：确认 Axure 是由 tools/axure-hook.ps1 启动的。"),
)


def _hint(message: str) -> str:
    for needle, text in _HINTS:
        if needle.lower() in message.lower():
            if sys.platform == "darwin":
                text = text.replace("tools/axure-hook.ps1", "python3 -m axure_mcp.launcher")
            return " — " + text
    return ""


def _validate_args(name: str, args: dict) -> None:
    """Reject bad arguments with a readable message instead of a Python TypeError."""
    schema = (_SCHEMAS.get(name) or {}).get("inputSchema") or {}
    _validate_value(args, schema, "arguments")


def _validate_value(value, spec, path):
    kind = spec.get("type")
    checks = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
    }
    if kind in checks and not checks[kind](value):
        raise AxureError(f"{path} should be {kind}, got {type(value).__name__}")
    if isinstance(value, float) and not math.isfinite(value):
        raise AxureError(f"{path} must be finite")
    if "enum" in spec and value not in spec["enum"]:
        raise AxureError(f"{path}={value!r} is not one of {spec['enum']}")
    for keyword, compare in (("minimum", lambda a,b: a < b), ("maximum", lambda a,b: a > b)):
        if keyword in spec and compare(value, spec[keyword]):
            raise AxureError(f"{path} violates {keyword}={spec[keyword]}")
    if isinstance(value, dict):
        props = spec.get("properties", {})
        missing = [key for key in spec.get("required", []) if key not in value]
        if missing:
            raise AxureError(f"{path}: missing required argument(s): {', '.join(missing)}")
        unknown = set(value) - set(props)
        if spec.get("additionalProperties") is False and unknown:
            raise AxureError(f"{path}: unknown argument(s): {', '.join(sorted(unknown))}")
        for key, item in value.items():
            _validate_value(item, props.get(key, {}), f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < spec.get("minItems", 0):
            raise AxureError(f"{path} has too few items")
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            raise AxureError(f"{path} has too many items")
        for index, item in enumerate(value):
            _validate_value(item, spec.get("items", {}), f"{path}[{index}]")


def _pages(app: Axure) -> list[dict]:
    return app.pages()


def _resolve_page(app: Axure, page_id: str | None, page_name: str | None) -> tuple[str, str]:
    pages = _pages(app)
    if not pages:
        raise AxureError("the open document has no pages")
    if page_id:
        for page in pages:
            if str(page["id"]).lower() == page_id.lower():
                return page["id"], page["name"]
        raise AxureError(f"no page with id {page_id}")
    if page_name:
        for page in pages:
            if (page["name"] or "").lower() == page_name.lower():
                return page["id"], page["name"]
        raise AxureError(f"no page named {page_name}")
    return pages[0]["id"], pages[0]["name"]


def _open(app: Axure, page_id: str | None, page_name: str | None) -> tuple[int, str, str]:
    resolved_id, resolved_name = _resolve_page(app, page_id, page_name)
    return app.open_page(resolved_id), resolved_id, resolved_name


# ---------------------------------------------------------------- tools

def _supported_tools(app):
    from .macos import MacAxure, SUPPORTED_TOOLS
    return SUPPORTED_TOOLS if isinstance(app, MacAxure) else HANDLERS.keys()


def tool_axure_status(_args: dict) -> dict:
    info = discover() or {}
    from .state import last_file
    try:
        app = _app()
        info = discover() or {}
        doc = app._doc()
        pages = _pages(app)
        return _json({
            "remembered_file": last_file(),
            "runtime": app.b.ping(),
            "supported_tools": sorted(_supported_tools(app)),
            "bridge": {"port": info.get("port"), "pid": info.get("pid")},
            "document": {"file": app.file_name(), "name": doc.get("displayName")},
            "pages": len(pages),
            "page_names": [p["name"] for p in pages][:20],
        })
    except (BridgeError, AxureError, OSError) as exc:
        return {"content": [_json({"remembered_file": last_file(),
                "bridge": {"port": info.get("port")}, "error": str(exc) + _hint(str(exc))})], "isError": True}


def tool_axure_list_pages(_args: dict) -> dict:
    app = _app()
    return _json({"file": app.file_name(), "pages": _pages(app)})


def tool_axure_get_page(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    widgets = app.widgets(package)
    return _json({
        "page": {"id": page_id, "name": page_name},
        "widget_count": len(widgets),
        "widgets": [{k: w.get(k) for k in
                     ("handle", "Name", "FriendlyTypeName", "Text", "X", "Y", "Width", "Height", "Visible")}
                    for w in widgets],
    })


def tool_axure_add_widget(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    handle = app.add_widget(
        package,
        kind=args["kind"],
        name=args.get("name") or "",
        text=args.get("text"),
        x=float(args.get("x", 0) or 0),
        y=float(args.get("y", 0) or 0),
        width=float(args.get("width", 160)),
        height=float(args.get("height", 40)),
    )
    info = app.widget_info(handle)
    app.save(package)
    return _json({"page": page_name, "widget": info, "saved": True})


def tool_axure_update_widget(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    info = app.update_widget(
        package,
        name=args.get("name") or "",
        handle=args.get("widget_id"),
        text=args.get("text"),
        x=args.get("x"),
        y=args.get("y"),
        width=args.get("width"),
        height=args.get("height"),
        visible=args.get("visible"),
        new_name=args.get("new_name"),
    )
    app.save(package)
    return _json({"page": page_name, "widget": info, "saved": True})


def tool_axure_delete_widget(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    ok = app.delete_widget(package, name=args.get("name") or "", handle=args.get("widget_id"))
    app.save(package)
    return _json({"page": page_name, "deleted": ok, "saved": True})


def tool_axure_render_page(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    png = app.render_page(page_id, int(args.get("max_dimension", 1400)))
    return {
        "content": [
            _text(f"Rendered page '{page_name}' ({len(png)} bytes PNG)."),
            {"type": "image", "data": base64.b64encode(png).decode("ascii"), "mimeType": "image/png"},
        ]
    }


def tool_axure_save(_args: dict) -> dict:
    app = _app()
    app.save()
    return _json({"saved": True, "file": app.file_name()})


# ---------------------------------------------------------------- page tools

def tool_axure_page_tree(_args: dict) -> dict:
    app = _app()
    return _json({"file": app.file_name(), "tree": app.page_tree()})


def tool_axure_create_page(args: dict) -> dict:
    app = _app()
    page = app.create_page(args["name"], args.get("parent_id"), int(args.get("index", 0) or 0))
    return _json({"page": page})


def tool_axure_create_folder(args: dict) -> dict:
    app = _app()
    folder = app.create_folder(args["name"], args.get("parent_id"), int(args.get("index", 0) or 0))
    return _json({"folder": folder})


def tool_axure_rename_page(args: dict) -> dict:
    app = _app()
    app.rename_page(args["page_id"], args["name"])
    return _json({"renamed": args["page_id"], "name": args["name"]})


def tool_axure_delete_page(args: dict) -> dict:
    app = _app()
    app.delete_page(args["page_id"])
    return _json({"deleted": args["page_id"]})


def tool_axure_move_page(args: dict) -> dict:
    app = _app()
    app.move_page(args["page_id"], args.get("parent_id"), int(args.get("index", 0) or 0))
    return _json({"moved": args["page_id"], "parent": args.get("parent_id")})


# ---------------------------------------------------------------- style tools

def _style_options(args: dict) -> dict:
    return {key: args[key] for key in STYLE_KEYS if args.get(key) is not None}


def tool_axure_style_widget(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    summary = app.style(package, name=args.get("name") or "", handle=args.get("widget_id"),
                        **_style_options(args))
    app.save(package)
    return _json({"page": page_name, "style": summary, "saved": True})


def tool_axure_get_widget_style(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    return _json({"page": page_name,
                  "style": app.widget_style(package, name=args.get("name") or "",
                                            handle=args.get("widget_id"))})


def tool_axure_z_order(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    names = app.z_order(package, list(args["names"]), args.get("action", "front"))
    app.save(package)
    return _json({"page": page_name, "reordered": names, "action": args.get("action", "front")})


# ---------------------------------------------------------------- interaction tools

def tool_axure_add_interaction(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    if args.get("actions"):
        # batch form: one event, one case, N actions - this is what keeps the tree reloadable
        result = app.add_actions(package, name=args.get("name") or "", handle=args.get("widget_id"),
                                 event=args["event"], actions=list(args["actions"]))
        app.save(package)
        return _json({"page": page_name, **result, "saved": True})
    result = app.add_interaction(package, name=args.get("name") or "", handle=args.get("widget_id"),
                                 event=args["event"], action=args.get("action") or "navigate",
                                 target_page_id=args.get("target_page_id"),
                                 link_type=args.get("link_type", "Current"),
                                 wait_ms=args.get("wait_ms"),
                                 target_widget=args.get("target_widget"),
                                 fade=args.get("fade"),
                                 value=args.get("value"),
                                 mode=args.get("mode"))
    app.save(package)
    return _json({"page": page_name, **result, "saved": True})


def tool_axure_get_interactions(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    widget = app.resolve_widget(package, args.get("name") or "", args.get("widget_id"))
    return _json({"page": page_name, "widget": app.widget_info(widget).get("Name"),
                  "interactions": app.interactions(widget)})


def tool_axure_align(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    moved = app.align(package, list(args["names"]), args["mode"],
                      args.get("reference", "bounds"))
    app.save(package)
    return _json({"page": page_name, "mode": args["mode"], "moved": moved, "saved": True})


def tool_axure_distribute(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    moved = app.distribute(package, list(args["names"]), args.get("axis", "horizontal"),
                           args.get("gap"))
    app.save(package)
    return _json({"page": page_name, "axis": args.get("axis", "horizontal"),
                  "moved": moved, "saved": True})


def tool_axure_set_list_items(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    result = app.set_list_items(package, args.get("name") or "", args.get("widget_id"),
                                list(args["items"]), args.get("selected"))
    app.save(package)
    return _json({"page": page_name, **result, "saved": True})


def tool_axure_open_file(args: dict) -> dict:
    app = _app()
    path = app.open_file(args["path"])
    try:  # remember it so an auto-relaunch comes back to the same document
        from .state import save as _save_state
        _save_state(file=path or args["path"])
    except Exception:  # noqa: BLE001 - best effort
        pass
    return _json({"opened": path})


def tool_axure_save_as(args: dict) -> dict:
    app = _app()
    return _json(app.save_as(args["path"]))


def tool_axure_set_page_note(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    result = app.set_page_note(package, args["text"], args.get("field"))
    app.save(package)
    return _json({"page": page_name, **result, "saved": True})


def tool_axure_get_page_notes(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    return _json({"page": page_name, "notes": app.page_notes(package)})


def tool_axure_set_widget_note(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    result = app.set_widget_note(package, args.get("name") or "", args.get("widget_id"),
                                 args["text"], args.get("field"))
    app.save(package)
    return _json({"page": page_name, **result, "saved": True})


def tool_axure_get_widget_notes(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    return _json({"page": page_name, "notes": app.widget_notes(package)})


def tool_axure_set_image(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    data = base64.b64decode(args["image_base64"])
    result = app.set_image(package, args.get("name") or "", args.get("widget_id"),
                           data, args.get("filename") or "image.png")
    app.save(package)
    return _json({"page": page_name, **result, "saved": True})


def tool_axure_set_table(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    result = app.set_table(package, args.get("name") or "", args.get("widget_id"),
                           args.get("rows"), args.get("columns"), args.get("data"),
                           args.get("row_heights"), args.get("column_widths"))
    app.save(package)
    return _json({"page": page_name, **result, "saved": True})


def tool_axure_set_rich_text(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    result = app.set_rich_text(package, args.get("name") or "", args.get("widget_id"),
                               args.get("text") or "", args.get("paragraphs"))
    app.save(package)
    return _json({"page": page_name, **result, "saved": True})


def tool_axure_same_size(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    result = app.same_size(package, list(args["names"]), args.get("mode", "both"))
    app.save(package)
    return _json({"page": page_name, "sized": result, "saved": True})


def tool_axure_panel_states(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    return _json({"page": page_name,
                  **app.panel_states(package, args.get("name") or "", args.get("widget_id"))})


def tool_axure_add_panel_state(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    result = app.add_panel_state(package, args.get("name") or "", args.get("widget_id"),
                                 int(args.get("count") or 1), args.get("state_name"))
    app.save(package)
    return _json({"page": page_name, **result, "saved": True})


def tool_axure_undo(_args: dict) -> dict:
    app = _app()
    app.undo()
    return _json({"undone": True})


def tool_axure_group(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    result = app.group(package, list(args["names"]), args.get("group_name") or "group")
    app.save(package)
    return _json({"page": page_name, **result, "saved": True})


def tool_axure_ungroup(args: dict) -> dict:
    app = _app()
    package, page_id, page_name = _open(app, args.get("page_id"), args.get("page_name"))
    result = app.ungroup(package, args.get("name") or "", args.get("widget_id"))
    app.save(package)
    return _json({"page": page_name, **result, "saved": True})


HANDLERS = {
    "axure_status": tool_axure_status,
    "axure_list_pages": tool_axure_list_pages,
    "axure_get_page": tool_axure_get_page,
    "axure_add_widget": tool_axure_add_widget,
    "axure_update_widget": tool_axure_update_widget,
    "axure_delete_widget": tool_axure_delete_widget,
    "axure_render_page": tool_axure_render_page,
    "axure_save": tool_axure_save,
    "axure_page_tree": tool_axure_page_tree,
    "axure_create_page": tool_axure_create_page,
    "axure_create_folder": tool_axure_create_folder,
    "axure_rename_page": tool_axure_rename_page,
    "axure_delete_page": tool_axure_delete_page,
    "axure_move_page": tool_axure_move_page,
    "axure_style_widget": tool_axure_style_widget,
    "axure_get_widget_style": tool_axure_get_widget_style,
    "axure_z_order": tool_axure_z_order,
    "axure_add_interaction": tool_axure_add_interaction,
    "axure_get_interactions": tool_axure_get_interactions,
    "axure_align": tool_axure_align,
    "axure_distribute": tool_axure_distribute,
    "axure_set_list_items": tool_axure_set_list_items,
    "axure_open_file": tool_axure_open_file,
    "axure_undo": tool_axure_undo,
    "axure_panel_states": tool_axure_panel_states,
    "axure_add_panel_state": tool_axure_add_panel_state,
    "axure_same_size": tool_axure_same_size,
    "axure_set_rich_text": tool_axure_set_rich_text,
    "axure_set_table": tool_axure_set_table,
    "axure_set_image": tool_axure_set_image,
    "axure_set_widget_note": tool_axure_set_widget_note,
    "axure_get_widget_notes": tool_axure_get_widget_notes,
    "axure_set_page_note": tool_axure_set_page_note,
    "axure_get_page_notes": tool_axure_get_page_notes,
    "axure_save_as": tool_axure_save_as,
    "axure_group": tool_axure_group,
    "axure_ungroup": tool_axure_ungroup,
}


for _tool in TOOLS:
    for _key, _spec in _tool["inputSchema"].get("properties", {}).items():
        if _key in {"width", "height", "border_width", "corner_radius", "font_size", "wait_ms", "index"}:
            _spec["minimum"] = 0
        if _key == "max_dimension":
            _spec.update(minimum=1, maximum=16384)
        if _key == "names":
            _spec["minItems"] = 1


_SCHEMAS: dict = {tool["name"]: tool for tool in TOOLS}


# ---------------------------------------------------------------- json-rpc loop

def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(msg_id, result) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def handle_request(request: dict) -> None:
    if not isinstance(request, dict):
        _error(None, -32600, "request must be an object")
        return
    msg_id = request.get("id")
    if (request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str)
            or ("id" in request and (isinstance(msg_id, bool) or not isinstance(msg_id, (str, int, type(None)))))):
        _error(None, -32600, "invalid JSON-RPC request")
        return
    if "id" not in request:
        return  # JSON-RPC notifications never receive a response
    if "params" in request and not isinstance(request["params"], dict):
        _error(msg_id, -32602, "params must be an object")
        return
    method = request.get("method")
    msg_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        _result(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return

    if method in ("notifications/initialized", "initialized",
                  "notifications/cancelled", "notifications/roots/list_changed"):
        # notifications normally carry no id; if a client sends one anyway, answer so it is not left
        # waiting for a reply that will never come
        if msg_id is not None:
            _result(msg_id, {})
        return

    if method == "ping":
        _result(msg_id, {})
        return

    if method == "tools/list":
        tools = TOOLS
        if sys.platform == "darwin":
            from .macos import SUPPORTED_TOOLS, SUPPORTED_WIDGETS
            tools = copy.deepcopy([t for t in TOOLS if t["name"] in SUPPORTED_TOOLS])
            for tool in tools:
                if tool["name"] == "axure_add_widget":
                    tool["inputSchema"]["properties"]["kind"]["enum"] = sorted(SUPPORTED_WIDGETS)
                    tool["description"] += " Mac: direct model insertion does not create an undo entry."
        _result(msg_id, {"tools": tools})
        return

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in HANDLERS:
            _error(msg_id, -32602, f"unknown tool: {name}")
            return
        args = params.get("arguments", {})
        handler = HANDLERS.get(name)
        if handler is None:
            _error(msg_id, -32602, f"unknown tool: {name}")
            return
        try:
            _validate_args(name, args)
            # serialise across sessions: one call may switch the window to its page, and a
            # concurrent call from another process would otherwise write to the wrong page
            with bridge_lock():
                if name != "axure_status":
                    app = _app()
                    if name not in _supported_tools(app):
                        raise AxureError(f"{name} is not supported by the Mac 11.0.0.4137 adapter; no changes made")
                payload = handler(args)
            if "content" not in payload:
                payload = {"content": [payload]}
        except (BridgeError, AxureError) as exc:
            message = f"{type(exc).__name__}: {exc}"
            _result(msg_id, {"content": [_text(message + _hint(str(exc)))], "isError": True})
            return
        except Exception as exc:  # noqa: BLE001 - surfaced to the model, not fatal
            _result(msg_id, {
                "content": [_text(f"{type(exc).__name__}: {exc}{_hint(str(exc))}"
                                  f"\n{traceback.format_exc()}")],
                "isError": True,
            })
            return
        _result(msg_id, payload)
        return

    if msg_id is not None:
        _error(msg_id, -32601, f"method not supported: {method}")


def main() -> int:
    # MCP stdio is UTF-8, including on Windows with a legacy console code page.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
        except ValueError:
            _error(None, -32700, "parse error")
            continue
        handle_request(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())















