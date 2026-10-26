"""High-level read/write operations on a live Axure RP 11 document.

Everything here was reverse-engineered out of AxureRP11.dll (see the notes in README.md). The
relevant internal API, all reached by reflection:

    client.qImj()                 the package (page) currently bound to the document window
    client.xImJ()                 its diagram
    client.TImN(packageId, ...)   open a package in the window - what clicking the sitemap does
    client.nIXw()                 the sitemap model
    diagram.DiagramObjects        XJ48.mJ4n, an IList<kJm2.KJm7>
    widget.YJ0S(rect, bool)       set geometry
    widget.Text / widget.Name     content
    doc.aNEQ() / doc.LNEY()       document settings / the .rp container store
    doc.Save()                    rewrite the file

Persisting an edit needs the live package to be re-serialized into the container first (see
Axure.commit); Document.Save() alone writes an unchanged document.
"""
from __future__ import annotations

import base64
import os
import shutil
import time
import uuid

from .client import Bridge, BridgeError

EMPTY_GUID = {"$guid": "00000000-0000-0000-0000-000000000000"}

#: friendly widget name -> internal class in AxureRP11.dll
WIDGET_TYPES = {
    "button": "Axure.DocumentModel.Objects.Button",
    "checkbox": "Axure.DocumentModel.Objects.Checkbox",
    "radiobutton": "Axure.DocumentModel.Objects.RadioButton",
    "textbox": "Axure.DocumentModel.Objects.TextBox",
    "textarea": "Axure.DocumentModel.Objects.TextArea",
    "droplist": "Axure.DocumentModel.Objects.ComboBox",
    "listbox": "Axure.DocumentModel.Objects.ListBox",
    "dynamicpanel": "Axure.DocumentModel.Objects.DynamicPanel",
    "image": "Axure.DocumentModel.Objects.ImageBox",
    "inlineframe": "Axure.DocumentModel.Objects.InlineFrame",
    "table": "Axure.DocumentModel.Objects.Table",
    "repeater": "Axure.DocumentModel.Objects.Repeater",
    "connector": "Axure.DocumentModel.Objects.Connector",
    "group": "Axure.DocumentModel.Objects.Layer",
    "hotspot": "Axure.DocumentModel.Objects.ImageMapRegion",
    "snapshot": "Axure.DocumentModel.Objects.Screenshot",
    "textlink": "Axure.DocumentModel.Objects.InlineLink",
    "shape": "Axure.DocumentModel.Objects.VectorShape",
}


class AxureError(RuntimeError):
    pass


class Axure:
    def __init__(self, port: int | None = None, document_id: str = "active", timeout: float = 180.0):
        self.b = Bridge(port, timeout=timeout).connect()
        self.document_id = document_id
        self._svc: int | None = None
        self._client: int | None = None

    # ---------- documents ----------

    def documents(self) -> list[dict]:
        docs = self.b.docs()
        return [d for d in (docs or []) if isinstance(d, dict) and "error" not in d]

    def _doc(self) -> dict:
        docs = self.documents()
        if not docs:
            raise AxureError("Axure is running but no document is open")
        return docs[0]

    @property
    def client(self) -> int:
        if self._client is None:
            self._client = self._doc()["handle"]
        return self._client

    @property
    def document(self) -> int:
        return self._doc()["docHandle"]

    @property
    def service(self) -> int:
        if self._svc is None:
            self._svc = self.b.ctor("Axure.McpServer.DocumentToolService",
                                    [{"$h": self.client}])["$h"]
        return self._svc

    def file_name(self) -> str:
        return self._doc().get("fileName") or ""

    def open_file(self, path: str) -> str:
        """Load an .rp into the open document (HNR1.LNRH.Load). Returns the new file path."""
        self.b.invoke_on(self.document, "Load", [str(path), None, False, "ReadWrite"])
        self._svc = None
        return self.file_name()

    # ---------- pages ----------

    def pages(self) -> list[dict]:
        sitemap = self.b.invoke_on(self.service, "GetSitemap", [self.document_id])
        tree = self.b.dump(sitemap["$h"], depth=8, limit=200)
        out: list[dict] = []

        def walk(nodes, in_page=False):
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                kind = node.get("Type")
                if kind == "page":
                    out.append({"id": node.get("Id"), "name": node.get("Name"), "type": kind})
                    continue
                walk((node.get("Children") or {}).get("items"))

        walk((tree or {}).get("Pages", {}).get("items"))
        return out

    def open_page(self, page_id: str) -> int:
        """Open a page in the document window; returns the LIVE package handle."""
        self.b.invoke_on(self.client, "TImN", [page_id, EMPTY_GUID, False])
        package = self.b.invoke_on(self.client, "qImj")
        if not isinstance(package, dict) or "$h" not in package:
            raise AxureError(f"page {page_id} could not be opened in the document window")
        return package["$h"]

    # ---------- sitemap writing ----------

    def sitemap(self) -> int:
        """The writable sitemap tree (nJhk.XJhE.Sitemap -> cNrY.eNrZ)."""
        model = self.b.invoke_on(self.client, "nIXw")
        return self.b.get_on(model["$h"], "Sitemap")["$h"]

    def page_tree(self, deep: bool = True) -> list[dict]:
        """Nested page tree read straight from the sitemap model.

        The read-only service DTO hands Children back as opaque handles, which is useless for
        assertions, so the tree is rebuilt here: payloads are Guid (page), kJsl (folder) or mJsm
        (component) - the same classification SitemapMapper does.
        """
        sitemap = self.sitemap()
        root = self.b.get_on(sitemap, "Tree")["$h"]
        return self._tree_children(sitemap, root, 0 if deep else -99)

    def _tree_children(self, sitemap: int, node: int, depth: int) -> list[dict]:
        out = []
        try:
            children = self.b.get_on(node, "Children")
        except BridgeError:
            return out
        for item in self.b.iter(children["$h"], limit=500) or []:
            handle = item.get("$h") if isinstance(item, dict) else None
            if not handle:
                continue
            try:
                name = self.b.invoke_on(sitemap, "IIw8", [{"$h": handle}])
                payload = self.b.invoke_on(sitemap, "xNFB", [{"$h": handle}])
            except BridgeError:
                continue
            if isinstance(payload, str):
                kind, ident = "page", payload
            elif isinstance(payload, dict) and "$h" in payload:
                type_name = payload.get("type") or ""
                if type_name.endswith("kJsl"):
                    kind = "folder"
                    ident = self.b.get_on(payload["$h"], "FolderId")
                else:
                    kind = "component"
                    ident = self.b.get_on(payload["$h"], "PackageId")
            else:
                kind, ident = "unknown", None
            # same key casing as the MCP DTO (Id/Name/Type/Children) so callers stay compatible
            entry = {"Id": ident, "Name": name, "Type": kind}
            if kind == "folder" and depth < 6:
                entry["Children"] = self._tree_children(sitemap, handle, depth + 1)
            out.append(entry)
        return out

    def _find_node(self, page_id: str) -> int:
        node = self.b.invoke_on(self.sitemap(), "iNFj", [{"$guid": page_id}])
        if not isinstance(node, dict) or "$h" not in node:
            raise AxureError(f"no sitemap node with id {page_id}")
        return node["$h"]

    def create_page(self, name: str, parent_id: str | None = None, index: int = 0) -> dict:
        """Create a page. Payload of a page node is a Guid = the page's package id."""
        sitemap = self.sitemap()
        parent = self._find_node(parent_id) if parent_id else self.b.get_on(sitemap, "Tree")["$h"]
        new_id = str(uuid.uuid4())
        node = self.b.invoke_on(sitemap, "TNFt", [None, {"$h": parent}, int(index), {"$guid": new_id}])
        self.b.invoke_on(sitemap, "XIwn", [{"$h": node["$h"]}, name])
        return {"id": new_id, "name": name, "type": "page"}

    def create_folder(self, name: str, parent_id: str | None = None, index: int = 0) -> dict:
        """Create a folder. Payload of a folder node is a WJsy.kJsl bound to a package of the
        document (it only uses it to reach the document), so we anchor it on the open page."""
        sitemap = self.sitemap()
        parent = self._find_node(parent_id) if parent_id else self.b.get_on(sitemap, "Tree")["$h"]
        anchor = self.active_package()
        if anchor is None:
            pages = self.pages()
            if not pages:
                raise AxureError("cannot create a folder in a document without pages")
            anchor = self.open_page(pages[0]["id"])
        folder = self.b.ctor("WJsy.kJsl", [{"$h": anchor}, name])
        folder_id = self.b.get_on(folder["$h"], "FolderId")
        node = self.b.invoke_on(sitemap, "TNFt", [None, {"$h": parent}, int(index), {"$h": folder["$h"]}])
        self.b.invoke_on(sitemap, "XIwn", [{"$h": node["$h"]}, name])
        return {"id": folder_id, "name": name, "type": "folder"}

    def rename_page(self, page_id: str, name: str) -> None:
        self.b.invoke_on(self.sitemap(), "XIwn", [{"$h": self._find_node(page_id)}, name])

    def delete_page(self, page_id: str) -> None:
        """Remove a page/folder node from the tree (node.qJlx() detaches it from its parent).

        Guarded on purpose: a wrong handle here detaches more than intended (a whole page run has
        disappeared that way). The node's payload is verified to match the requested id, and the last
        remaining page is never removed.
        """
        # count via the sitemap model, not the read-only service: a service call right after a
        # move_page has been observed to kill the process
        try:
            root = self.b.get_on(self.sitemap(), "Tree")["$h"]
            children = self.b.get_on(root, "Children")["$h"]
            if int(self.b.get_on(children, "Count")) <= 1:
                raise AxureError("refusing to delete the last page in the document")
        except BridgeError:
            pass
        node = self._find_node(page_id)
        try:
            payload = self.b.invoke_on(self.sitemap(), "xNFB", [{"$h": node}])
            looks_like_page = isinstance(payload, str)
            looks_like_folder = (isinstance(payload, dict)
                                 and (payload.get("type") or "").endswith("kJsl"))
            if not (looks_like_page or looks_like_folder):
                raise AxureError("that node is not a page or folder")
            if looks_like_page and payload != page_id:
                raise AxureError("node payload does not match the requested page id")
        except BridgeError:
            pass
        self.b.invoke_on(node, "qJlx")

    def move_page(self, page_id: str, parent_id: str | None = None, index: int = 0) -> None:
        """Move a node. The tree does not reparent on insert, so detach first (node.qJlx).

        Re-opening the page afterwards matters: writing to a page that was just moved used to hang
        the whole app (the window still pointed at the pre-move package). Opening it again refreshes
        the cache so subsequent edits go to the right place.
        """
        sitemap = self.sitemap()
        parent = self._find_node(parent_id) if parent_id else self.b.get_on(sitemap, "Tree")["$h"]
        node = self._find_node(page_id)
        self.b.invoke_on(node, "qJlx")
        self.b.invoke_on(sitemap, "MNF5", [{"$h": node}, {"$h": parent}, int(index)])
        try:
            self.open_page(page_id)
        except BridgeError:
            pass

    def active_package(self) -> int | None:
        package = self.b.invoke_on(self.client, "qImj")
        return package["$h"] if isinstance(package, dict) else None

    def diagram(self, package: int) -> int:
        return self.b.get_on(package, "Diagram")["$h"]

    def objects(self, package: int) -> int:
        return self.b.get_on(self.diagram(package), "DiagramObjects")["$h"]

    def widget_count(self, package: int) -> int:
        return self.b.get_on(self.objects(package), "Count")

    def active_diagram_id(self) -> str | None:
        try:
            value = self.b.get_on(self.client, "ActiveDiagramId")
        except BridgeError:
            return None
        return value

    # ---------- widgets ----------

    def add_widget(self, package: int, kind: str, name: str = "", text: str | None = None,
                   x: float = 0, y: float = 0, width: float = 120, height: float = 40,
                   extra: dict | None = None, shape: str = "Rectangle") -> int:
        kind_key = kind.lower()
        type_name = WIDGET_TYPES.get(kind_key)
        if type_name is None:
            raise AxureError(f"unknown widget kind {kind!r}; known: {sorted(WIDGET_TYPES)}")

        if kind_key == "shape":
            # VectorShape(btdo, Axure.Platform.Shape, name, VectorLegacyType, bool)
            widget = self.b.ctor(type_name, [{"$h": package}, shape,
                                             name or "shape", "None", False])
        else:
            widget = self.b.ctor(type_name, [{"$h": package}])

        handle = widget["$h"]
        self.set_rect(handle, x, y, width, height)
        if name:
            self.b.set_on(handle, "Name", name)
        if text is not None:
            self.b.set_on(handle, "Text", str(text))
        elif kind_key == "shape":
            # a VectorShape renders its name as its caption, which looks like a bug on a background card
            self.b.set_on(handle, "Text", "")
        for key, value in (extra or {}).items():
            self.b.set_on(handle, key, value)
        return self._insert_widget(package, handle, x, y)

    def _insert_widget(self, package: int, widget: int, x: float, y: float) -> int:
        """Insert a widget through the editor's own pipeline so the edit lands on the undo stack.

        Two things matter here:
        * a plain DiagramObjects.Add() shows up on the canvas but is invisible to Ctrl+Z, while
          editor.xFUI() + helper.zjzI() is exactly what dragging a widget from the toolbox does;
        * zjzI() *copies* the widget into the live diagram at (point + the widget's own rect), so
          the copy - not the original - is what the document now holds. Passing (0, 0) keeps the
          widget's own rect authoritative, and the returned handle is the live copy.
        """
        diagram = self.diagram(package)
        objects = self.objects(package)
        before = self._id_set(objects)
        try:
            owner = self.b.invoke_on(diagram, "btnD")
            temp = self.b.ctor("hJat.OJa9", [{"$h": owner["$h"]}])["$h"]
            scratch = self.b.get_on(temp, "DiagramObjects")["$h"]
            self.b.invoke_on(scratch, "cJ4T", [{"$arr": [{"$h": widget}]}])
            editor = self.b.get_on(self.client, "Editor")["$h"]
            payload = self.b.invoke_on(editor, "xFUI",
                                       [{"$h": temp}, {"$h": scratch}, False, None, True, False])
            helper = self.b.get_on(editor, "LFyv")["$h"]
            origin = self.b.ctor("m5wr.f5wV", [0.0, 0.0])
            self.b.invoke_on(helper, "zjzI",
                             [{"$h": payload["$h"]}, False, {"$h": origin["$h"]}, False, False])
        except (BridgeError, AxureError):
            self.b.invoke_on(objects, "Add", [{"$h": widget}])
            return widget

        for handle in set(self._id_set(objects)) - set(before):
            return self._id_set(objects)[handle]
        return widget

    def _id_set(self, objects: int) -> dict:
        """{identity: handle} for the widgets in a collection (bridge handles are not stable)."""
        out = {}
        for item in self.b.iter(objects, limit=4000) or []:
            handle = item.get("$h") if isinstance(item, dict) else None
            if handle:
                try:
                    out[self.b.identity(handle)["id"]] = handle
                except BridgeError:
                    continue
        return out

    def set_rect(self, widget: int, x: float, y: float, width: float, height: float) -> None:
        rect = self.b.ctor("h5uH.j5u5", [float(x), float(y), float(width), float(height)])
        self.b.invoke_on(widget, "YJ0S", [{"$h": rect["$h"]}, True])

    def widget_info(self, widget: int) -> dict:
        info: dict = {}
        for prop in ("Name", "TypeName", "FriendlyTypeName", "Text",
                     "X", "Y", "Width", "Height", "Visible", "IsLocked"):
            try:
                info[prop] = self.b.get_on(widget, prop)
            except BridgeError:
                pass
        info["handle"] = widget
        return info

    def widgets(self, package: int, deep: bool = False) -> list[dict]:
        """Widgets on the page. deep=True also walks into groups/layers.

        Deep walking stays opt-in: touching the DiagramObjects of every child object (table cells,
        panel states) is expensive and, for some containers, can destabilise the app.
        """
        out = []
        for handle in self._collect_handles(self.objects(package), deep):
            info = self.widget_info(handle)
            if info.get("Name"):
                out.append(info)
        return out

    def _collect_handles(self, objects: int, deep: bool = False, depth: int = 0) -> list[int]:
        handles = []
        for item in self.b.iter(objects, limit=2000) or []:
            handle = item.get("$h") if isinstance(item, dict) else None
            if not handle:
                continue
            handles.append(handle)
            if deep and depth < 4:
                try:
                    inner = self.b.get_on(handle, "DiagramObjects")
                except BridgeError:
                    continue
                if isinstance(inner, dict) and "$h" in inner:
                    handles.extend(self._collect_handles(inner["$h"], deep, depth + 1))
        return handles

    def find_widget(self, package: int, name: str) -> int | None:
        """Look a widget up by name; if it is not on the top level, descend into groups."""
        objects = self.objects(package)
        for item in self.b.iter(objects, limit=2000) or []:
            handle = item.get("$h") if isinstance(item, dict) else None
            if not handle:
                continue
            try:
                if self.b.get_on(handle, "Name") == name:
                    return handle
            except BridgeError:
                continue
        for handle in self._collect_handles(objects, deep=True):
            try:
                if self.b.get_on(handle, "Name") == name:
                    return handle
            except BridgeError:
                continue
        return None

    # ---------- styling ----------

    @staticmethod
    def _colour(bridge: Bridge, hex_or_rgb) -> int:
        """m5k3 from '#RRGGBB' / '#AARRGGBB' / (r, g, b[, a])."""
        if isinstance(hex_or_rgb, (tuple, list)):
            parts = list(hex_or_rgb) + [255] * (4 - len(hex_or_rgb))
            r, g, bl, a = (int(v) for v in parts[:4])
        else:
            text = str(hex_or_rgb).strip().lstrip("#")
            if len(text) == 3:
                text = "".join(c * 2 for c in text)
            if len(text) == 6:
                r, g, bl = (int(text[i:i + 2], 16) for i in (0, 2, 4))
                a = 255
            elif len(text) == 8:
                a, r, g, bl = (int(text[i:i + 2], 16) for i in (0, 2, 4, 6))
            else:
                raise AxureError(f"bad colour {hex_or_rgb!r}; use #RRGGBB or #AARRGGBB")
        argb = ((a & 0xFF) << 24) | ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (bl & 0xFF)
        return bridge.invoke("B5kg.m5k3", "w5kx", [argb])["$h"]

    def _fill(self, hex_or_rgb) -> int:
        """A fresh IHVp.uHV0 fill. Never mutate a style's existing fill: style objects are shared
        between widgets, so in-place edits leak onto every widget using the same default style."""
        return self.b.ctor("IHVp.uHV0", [{"$h": self._colour(self.b, hex_or_rgb)}])["$h"]

    def style(self, package: int, name: str = "", handle: int | None = None, **options) -> dict:
        """Set visual style properties on one widget.

        Supported option keys: fill, text_color, border_color, border_width, corner_radius,
        opacity, font, font_size, bold, italic, underline, strikethrough, align, valign,
        line_spacing, letter_spacing, padding (list of 1-4), border_visible, rotations.
        """
        widget = self.resolve_widget(package, name, handle)
        style = self.b.invoke_on(widget, "vJOH")["$h"]
        applied: dict = {}

        def put(prop, value):
            self.b.set_on(style, prop, value)
            applied[prop] = value

        for key, prop in (("fill", "Fill"), ("text_color", "ForeGroundFill"),
                          ("border_color", "BorderFill")):
            if options.get(key) is not None:
                self.b.set_on(style, prop, {"$h": self._fill(options[key])})
                applied[key] = options[key]

        scalars = {
            "border_width": "BorderWidth",
            "corner_radius": "CornerRadius",
            "opacity": "Opacity",
            "font_size": "FontSize",
            "line_spacing": "LineSpacing",
            "letter_spacing": "CharacterSpacing",
            "rotation": "Rotation",
        }
        for key, prop in scalars.items():
            if options.get(key) is not None:
                put(prop, float(options[key]))

        if options.get("font") is not None:
            put("FontName", str(options["font"]))

        for key, prop in (("bold", "Bold"), ("italic", "Italic")):
            if options.get(key) is not None:
                put(prop, "TRUE" if options[key] else "FALSE")

        for key, prop in (("underline", "Underline"), ("strikethrough", "Strikethrough")):
            if options.get(key) is not None:
                put(prop, bool(options[key]))

        align_map = {"left": "Near", "center": "Center", "centre": "Center",
                     "right": "Far", "justify": "Justify"}
        if options.get("align"):
            put("HorizontalAlignment", align_map[str(options["align"]).lower()])
        if options.get("valign"):
            valign_map = {"top": "Near", "middle": "Center", "center": "Center",
                          "centre": "Center", "bottom": "Far"}
            put("VerticalAlignment", valign_map[str(options["valign"]).lower()])

        padding = options.get("padding")
        if padding:
            values = list(padding) if isinstance(padding, (list, tuple)) else [padding] * 4
            values = (values + values[-1:] * 4)[:4]
            for prop, value in zip(("PaddingLeft", "PaddingTop", "PaddingRight", "PaddingBottom"), values):
                put(prop, float(value))

        if options.get("border_visible") is not None:
            put("BorderVisibility", "All" if options["border_visible"] else "None")

        if options.get("line_pattern"):
            put("LinePattern", str(options["line_pattern"]))

        return self._style_summary(style)

    def _style_summary(self, style: int) -> dict:
        summary = {}
        for prop in ("FontName", "FontSize", "Bold", "Italic", "Underline",
                     "Opacity", "CornerRadius", "BorderWidth", "HorizontalAlignment",
                     "VerticalAlignment", "LineSpacing"):
            try:
                summary[prop] = self.b.get_on(style, prop)
            except BridgeError:
                pass
        for prop in ("Fill", "ForeGroundFill", "BorderFill"):
            try:
                fill = self.b.get_on(style, prop)
                if isinstance(fill, dict) and "$h" in fill:
                    dump = self.b.dump(fill["$h"], depth=2, limit=4) or {}
                    colour = dump.get("Color") or {}
                    if isinstance(colour, dict) and "$h" in colour:
                        r, g, bl = self._rgb(colour["$h"])
                        summary[prop] = "#{:02X}{:02X}{:02X}".format(r, g, bl)
            except BridgeError:
                pass
        return summary

    def _rgb(self, colour_handle: int) -> tuple[int, int, int]:
        """m5k3 keeps the channels in four private byte fields; only A/R/B have properties."""
        values = {}
        for name in ("p5ex", "e5ee", "R5eM", "i5eC", "R", "B"):
            try:
                values[name] = int(self.b.get_on(colour_handle, name) or 0)
            except BridgeError:
                pass
        a = values.get("p5ex", values.get("A", 255))
        r = values.get("e5ee", values.get("R", 0))
        g = values.get("R5eM", 0)
        bl = values.get("i5eC", values.get("B", 0))
        return r, g, bl

    def widget_style(self, package: int, name: str = "", handle: int | None = None) -> dict:
        widget = self.resolve_widget(package, name, handle)
        return self._style_summary(self.b.invoke_on(widget, "vJOH")["$h"])

    # ---------- interactions ----------

    def _ix_map(self, widget: int) -> int:
        """KJm7.GIss() -> MJuB.gJuG, the widget's interaction map (itself a tree of nodes)."""
        return self.b.invoke_on(widget, "GIss")["$h"]

    def interactions(self, widget: int) -> list[dict]:
        ix = self._ix_map(widget)
        events = self.b.invoke_on(ix, "SJuj")
        out = []
        for item in (self.b.iter(events["$h"], limit=100) if isinstance(events, dict) else []):
            handle = item.get("$h") if isinstance(item, dict) else None
            if not handle:
                continue
            record = {"type": self.b.get_on(handle, "EventType"),
                      "cases": []}
            cases = self.b.invoke_on(ix, "YJuS", [{"$h": handle}])
            for case in (self.b.iter(cases["$h"], limit=100) if isinstance(cases, dict) else []):
                ch = case.get("$h") if isinstance(case, dict) else None
                if not ch:
                    continue
                entry = {"description": None, "actions": []}
                try:
                    entry["description"] = self.b.get_on(ch, "Description")
                except BridgeError:
                    pass
                acts = self.b.invoke_on(ix, "vJu1", [{"$h": ch}])
                for action in (self.b.iter(acts["$h"], limit=100) if isinstance(acts, dict) else []):
                    ah = action.get("$h") if isinstance(action, dict) else None
                    if not ah:
                        continue
                    info = {"type": None, "text": None}
                    for prop in ("TypeName", "EditorText", "ActionDescription"):
                        try:
                            info[prop] = self.b.get_on(ah, prop)
                        except BridgeError:
                            pass
                    entry["actions"].append(info)
                record["cases"].append(entry)
            out.append(record)
        return out

    def add_interaction(self, package: int, name: str = "", handle: int | None = None,
                        event: str = "OnClick", action: str = "navigate",
                        target_page_id: str | None = None, link_type: str = "Current",
                        wait_ms: int | None = None, target_widget: str | None = None,
                        fade: str | None = None, value: float | None = None,
                        mode: str | None = None) -> dict:
        """Attach a single action to an event (shorthand for add_actions)."""
        widget = self.resolve_widget(package, name, handle)
        return self.add_actions(package, handle=widget, event=event, actions=[{
            "action": action, "target_page_id": target_page_id, "link_type": link_type,
            "wait_ms": wait_ms, "target_widget": target_widget, "fade": fade,
            "value": value, "mode": mode}])

    def add_actions(self, package: int, name: str = "", handle: int | None = None,
                    event: str = "OnClick", actions: list[dict] | None = None) -> dict:
        """Add several actions to ONE event in a single pass.

        Adding actions one call at a time can leave the interaction tree in a shape Axure
        refuses to reload (observed: InvalidCastException, an action ending up inside the page's
        widget collection). Doing the whole event at once keeps the tree consistent:
        one event, one case, N actions.
        """
        actions = list(actions or [])
        if not actions:
            raise AxureError("add_actions needs at least one action")
        widget = self.resolve_widget(package, name, handle)
        ix = self._ix_map(widget)
        root = self.b.get_on(ix, "Tree")["$h"]

        event_node = self.b.invoke_on(ix, "fJuI", [event])
        if not (isinstance(event_node, dict) and "$h" in event_node and event_node.get("$h")):
            ix_event = self.b.ctor("Axure.DocumentModel.Interaction.IxEvent",
                                   [{"$h": package}, event])
            node = self.b.invoke_on(ix, "TNFt", [None, {"$h": root}, 0, {"$h": ix_event["$h"]}])
            event_node = {"$h": node["$h"]}

        case = self.b.ctor("Axure.DocumentModel.Interaction.IxCase", [{"$h": package}])
        case_node = self.b.invoke_on(ix, "TNFt",
                                     [None, {"$h": event_node["$h"]}, 0, {"$h": case["$h"]}])

        inserted = 0
        for spec in actions:
            handle_action = self._build_action(
                package, spec.get("action", "navigate"), spec.get("target_page_id"),
                spec.get("link_type", "Current"), spec.get("wait_ms"),
                spec.get("target_widget"), spec.get("fade"), spec.get("value"),
                spec.get("mode"))
            self.b.invoke_on(ix, "TNFt", [None, {"$h": case_node["$h"]}, 0,
                                           {"$h": handle_action}])
            inserted += 1
        return {"widget": self.widget_info(widget).get("Name"), "event": event,
                "actions": inserted, "interactions": self.interactions(widget)}

    # Actions that look fine in memory but do NOT survive a save/reload: the action node is silently
    # dropped by Axure's serialiser, and IxFadeWidgetAction goes further - the reloaded document then
    # throws InvalidCastException (an action ends up where a widget is expected). Both were caught by
    # a save→reload audit, not by reading EditorText.
    UNSAFE_ACTIONS = {
        "show_hide": "IxFadeWidgetAction: after save+reload the document throws InvalidCastException",
        "scroll_to": "the action node is dropped on save",
        "focus": "the action node is dropped on save",
        "set_image": "the action node is dropped on save",
        "apply_style": "the action node is dropped on save",
        "enable_disable": "the action node is dropped on save",
        "link_frame": "the action node is dropped on save",
        "panel_order": "the action node is dropped on save",
        "expand_collapse": "the action node is dropped on save",
        "set_panel_state": "IxSetPanelStateAction + DIyv leaves a document that cannot be loaded",
    }

    def _build_action(self, package: int, action: str, target_page_id=None, link_type="Current",
                      wait_ms=None, target_widget=None, fade=None, value=None, mode=None) -> int:
        """Create one IxAction object and return its handle (nothing is inserted yet)."""
        reason = self.UNSAFE_ACTIONS.get(str(action).lower())
        if reason:
            raise AxureError(
                f"action {action!r} is disabled: {reason}. Only navigate, close, wait, "
                "set_opacity, move, rotate and resize survive a save/reload audit.")
        action_lower = action.lower()
        if action_lower in ("navigate", "openlink", "link"):
            if not target_page_id:
                raise AxureError("navigate action needs target_page_id")
            ix_action = self.b.ctor("Axure.DocumentModel.Interaction.IxLinkWindowAction",
                                    [{"$h": package}, {"$guid": target_page_id}, link_type])
        elif action_lower in ("close", "closecurrent"):
            ix_action = self.b.ctor("Axure.DocumentModel.Interaction.IxCloseCurrentAction",
                                    [{"$h": package}])
        elif action_lower == "wait":
            ix_action = self.b.ctor("Axure.DocumentModel.Interaction.IxWaitAction",
                                    [{"$h": package}])
            self.b.set_on(ix_action["$h"], "WaitTime", int(wait_ms or 500))
        elif action_lower in ("set_panel_state", "set_state"):
            if not target_widget:
                raise AxureError("set_panel_state needs target_widget (a dynamic panel)")
            if not value:
                raise AxureError("set_panel_state needs value (the state name)")
            panel = self.resolve_widget(package, target_widget)
            # the only usable ctor takes the animation durations; the state goes in by path
            durations = self.b.call("makedict", key="System.String", value="System.Double")
            ix_action = self.b.ctor("Axure.DocumentModel.Interaction.IxSetPanelStateAction",
                                    [{"$h": package}, {"$h": durations["$h"]}])
            path = self.b.ctor("sJ8l.dJ8s",
                               [{"$h": package}, {"$guid": self.b.invoke_on(panel, "Mtn3")}])
            self.b.invoke_on(ix_action["$h"], "DIyv", [{"$h": path["$h"]}, str(value)])
        elif action_lower in ("set_opacity", "move", "rotate", "resize", "set_image", "apply_style"):
            if not target_widget:
                raise AxureError(f"action {action_lower} needs target_widget")
            target = self.resolve_widget(package, target_widget)
            guid = self.b.invoke_on(target, "Mtn3")
            if action_lower in ("set_image", "apply_style"):
                cls = ("Axure.DocumentModel.Interaction.IxSetImageAction"
                       if action_lower == "set_image"
                       else "Axure.DocumentModel.Interaction.IxApplyStyleAction")
                ix_action = self.b.ctor(cls, [{"$h": package}, {"$guid": guid}])
            else:
                cls = {
                    "set_opacity": "Axure.DocumentModel.Interaction.IxSetOpacityAction",
                    "move": "Axure.DocumentModel.Interaction.IxMoveWidgetAction",
                    "rotate": "Axure.DocumentModel.Interaction.IxRotateWidgetAction",
                    "resize": "Axure.DocumentModel.Interaction.IxSetWidgetSizeAction",
                }[action_lower]
                if value is None:
                    raise AxureError(f"action {action_lower} needs value")
                # these actions keep a Dictionary<string, double> of per-target numbers and are
                # pointed at a widget through a path object, not through the constructor
                table = self.b.call("makedict", key="System.String", value="System.Double")
                ix_action = self.b.ctor(cls, [{"$h": package}, {"$h": table["$h"]}])
                path = self.b.ctor("sJ8l.dJ8s", [{"$h": package}, {"$guid": guid}])
                self.b.invoke_on(ix_action["$h"], "DIyv", [{"$h": path["$h"]}, str(value)])
        elif action_lower in ("enable_disable", "link_frame", "panel_order", "expand_collapse"):
            if not target_widget:
                raise AxureError(f"action {action_lower} needs target_widget")
            target = self.resolve_widget(package, target_widget)
            guid = self.b.invoke_on(target, "Mtn3")
            flag = bool(value) if value is not None else True
            if action_lower == "enable_disable":
                # (btdo, Guid, EnableActionType) - the 3-arg form is what disambiguates the overload
                ix_action = self.b.ctor(
                    "Axure.DocumentModel.Interaction.IxEnableDisableWidgetsAction",
                    [{"$h": package}, {"$guid": guid}, mode or "Enable"])
            elif action_lower == "link_frame":
                ix_action = self.b.ctor(
                    "Axure.DocumentModel.Interaction.IxLinkFrameAction",
                    [{"$h": package}, {"$guid": guid}, link_type])
            elif action_lower == "panel_order":
                ix_action = self.b.ctor(
                    "Axure.DocumentModel.Interaction.IxSetPanelOrderAction",
                    [{"$h": package}, {"$guid": guid}, flag])
            else:
                ix_action = self.b.ctor(
                    "Axure.DocumentModel.Interaction.IxExpandCollapseTreeAction",
                    [{"$h": package}, {"$guid": guid}, flag])
        elif action_lower in ("scroll_to", "focus", "show_hide"):
            if not target_widget:
                raise AxureError(f"action {action_lower} needs target_widget")
            target = self.resolve_widget(package, target_widget)
            target_guid = self.b.invoke_on(target, "Mtn3")
            if action_lower == "show_hide":
                # IxFadeWidgetAction is Axure's "Show/Hide" action; FadeActionType = Hide/Show/Toggle
                ix_action = self.b.ctor("Axure.DocumentModel.Interaction.IxFadeWidgetAction",
                                        [{"$h": package}, {"$guid": target_guid}])
                if fade:
                    self.b.set_on(ix_action["$h"], "DefaultFadeAction", fade)
            else:
                cls = ("Axure.DocumentModel.Interaction.IxScrollToWidgetAction"
                       if action_lower == "scroll_to"
                       else "Axure.DocumentModel.Interaction.IxSetFocusOnWidgetAction")
                ix_action = self.b.ctor(cls, [{"$h": package}, {"$guid": target_guid}])
        else:
            raise AxureError(
                f"unknown action {action!r}; supported: navigate, close, wait, scroll_to, focus, "
                "show_hide, set_panel_state, set_opacity, move, rotate, resize, set_image, "
                "apply_style, enable_disable, link_frame, panel_order, expand_collapse")

        return ix_action["$h"]

    def clear_interactions(self, widget: int) -> None:
        ix = self._ix_map(widget)
        for item in self.b.iter((self.b.invoke_on(ix, "SJuj") or {}).get("$h", 0), limit=100) or []:
            node = item.get("$h") if isinstance(item, dict) else None
            if node:
                self.b.invoke_on(node, "qJlx")

    # ---------- notes / annotations ----------

    def _annotation(self, package: int):
        return self.b.get_on(package, "Annotation")

    def _annotation_fields(self, annotation: int) -> list[dict]:
        """Field entries for a PAGE annotation.

        `ann.CJih()` can hand back the widget ("DiagramObject") field set once widget notes exist,
        which made page_notes() read the widget note text back. The page field set has to be asked
        for explicitly from the document model, mirroring how the widget one is obtained.
        """
        model = self.b.invoke_on(self.client, "nIXw")["$h"]
        holder = self.b.get_on(model, "PageFragmentAnnotationFieldSet")
        if isinstance(holder, dict) and "$h" in holder:
            field_set = self.b.invoke_on(holder["$h"], "K5HC")
            if isinstance(field_set, dict) and "$h" in field_set:
                return self._field_entries(field_set["$h"])
        field_set = self.b.invoke_on(annotation, "CJih")
        if not isinstance(field_set, dict) or "$h" not in field_set:
            return []
        return self._field_entries(field_set["$h"])

    def page_notes(self, package: int) -> dict:
        annotation = self._annotation(package)
        if not isinstance(annotation, dict) or "$h" not in annotation:
            return {}
        handle = annotation["$h"]
        out = {}
        for field in self._annotation_fields(handle):
            try:
                out[field["name"]] = self.b.invoke_on(handle, "bJi1", [field["name"]])
            except BridgeError:
                out[field["name"]] = None
        return out

    def set_page_note(self, package: int, text: str, field: str | None = None) -> dict:
        """Write a page note.

        The value is rich text (tSmX.rSma): pull the field's current value with gJiI(propertyId),
        put the string on its Text, then hand it back through XJi5(fieldName, rSma). That is what
        makes the note appear in the Notes panel and persist.
        """
        annotation = self._annotation(package)
        if not isinstance(annotation, dict) or "$h" not in annotation:
            raise AxureError("page has no annotation object")
        handle = annotation["$h"]
        fields = self._annotation_fields(handle)
        if not fields:
            raise AxureError("this document defines no annotation fields")
        target = None
        for entry in fields:
            if field is None or entry["name"] == field:
                target = entry
                break
        if target is None:
            raise AxureError(f"unknown note field {field!r}; available: "
                             f"{[f['name'] for f in fields]}")

        rich = self.b.invoke_on(handle, "gJiI", [{"$guid": target["id"]}])
        rich_handle = rich["$h"]
        self.b.set_on(rich_handle, "Text", str(text))
        self.b.invoke_on(handle, "XJi5", [target["name"], {"$h": rich_handle}])
        return {"field": target["name"], "text": str(text),
                "empty": self.b.get_on(handle, "IsEmpty")}

    def set_widget_note(self, package: int, name: str = "", handle: int | None = None,
                        text: str = "", field: str | None = None) -> dict:
        """Attach a note to one widget.

        A widget note is a UJiJ living in the diagram's annotation collection (diagram.zIuI()),
        keyed by OwnerId. A fresh UJiJ needs three things before it can hold text:
        vJi6(guid) to bind the owner, JJik("DiagramObject") to set its kind, and the *widget* field
        set (model.DiagramObjectAnnotationFieldSet.K5HC()) for the property ids - using the page
        field set silently returns the page note instead.
        """
        widget = self.resolve_widget(package, name, handle)
        # tJiN.UJiJ instances share storage: once a page note exists, a widget note silently
        # overwrites it after save/reload. Refuse instead of losing data.
        existing_page = self.page_notes(package)
        if any(v for v in existing_page.values()):
            raise AxureError(
                "this page already has a page note; Axure shares annotation storage between page and "
                "widget notes, so writing both ends up overwriting the page note after a reload. "
                "Use one kind of note per page (clear the page note first if you need widget notes).")
        guid = self.b.invoke_on(widget, "Mtn3")
        entries = self._widget_note_fields()
        if not entries:
            raise AxureError("this document defines no widget note fields")
        target = next((e for e in entries if field is None or e["name"] == field), None)
        if target is None:
            raise AxureError(f"unknown widget note field {field!r}; available: "
                             f"{[e['name'] for e in entries]}")

        # The (package, guid) ctor keeps page notes safe but the note does not survive a reload;
        # the (package) ctor persists but shares storage with the page annotation. Persistence wins,
        # and the guard above stops it from being used on a page that already has a page note.
        annotation = self.b.ctor("tJiN.UJiJ", [{"$h": package}])["$h"]
        self.b.invoke_on(annotation, "vJi6", [{"$guid": guid}])
        self.b.invoke_on(annotation, "JJik", ["DiagramObject"])
        rich = self.b.invoke_on(annotation, "gJiI", [{"$guid": target["id"]}])["$h"]
        self.b.set_on(rich, "Text", str(text))
        self.b.invoke_on(annotation, "XJi5", [target["name"], {"$h": rich}])

        collection = self.b.invoke_on(self.diagram(package), "zIuI")["$h"]
        existing = self._widget_annotation(collection, guid)
        if existing:
            self.b.invoke_on(collection, "Remove", [{"$h": existing}])
        self.b.invoke_on(collection, "Add", [{"$h": annotation}])
        return {"widget": self.widget_info(widget).get("Name"), "field": target["name"],
                "note": str(text)}

    def _widget_note_fields(self) -> list[dict]:
        model = self.b.invoke_on(self.client, "nIXw")["$h"]
        holder = self.b.get_on(model, "DiagramObjectAnnotationFieldSet")
        if not isinstance(holder, dict) or "$h" not in holder:
            return []
        field_set = self.b.invoke_on(holder["$h"], "K5HC")
        return self._field_entries(field_set["$h"]) if isinstance(field_set, dict) else []

    def _field_entries(self, field_set: int) -> list[dict]:
        seq = self.b.invoke_on(field_set, "W51X")
        entries = []
        for item in (self.b.iter(seq["$h"], limit=100) if isinstance(seq, dict) else []) or []:
            handle = item.get("$h") if isinstance(item, dict) else None
            if handle:
                entries.append({"name": self.b.get_on(handle, "Name"),
                                "id": self.b.get_on(handle, "PropertyNameId")})
        return entries

    def _widget_annotation(self, collection: int, guid: str) -> int | None:
        seq = self.b.invoke_on(collection, "QJPN")
        for item in (self.b.iter(seq["$h"], limit=500) if isinstance(seq, dict) else []) or []:
            handle = item.get("$h") if isinstance(item, dict) else None
            if handle and self.b.get_on(handle, "OwnerId") == guid:
                return handle
        return None

    def widget_notes(self, package: int) -> list[dict]:
        collection = self.b.invoke_on(self.diagram(package), "zIuI")["$h"]
        seq = self.b.invoke_on(collection, "QJPN")
        out = []
        for item in (self.b.iter(seq["$h"], limit=500) if isinstance(seq, dict) else []) or []:
            handle = item.get("$h") if isinstance(item, dict) else None
            if not handle:
                continue
            owner = self.b.get_on(handle, "OwnerId")
            entry = {"owner": owner, "kind": self.b.invoke_on(handle, "DJiE"), "fields": {}}
            for field in self._widget_note_fields():
                try:
                    entry["fields"][field["name"]] = self.b.invoke_on(handle, "bJi1",
                                                                      [field["name"]])
                except BridgeError:
                    pass
            out.append(entry)
        return out

    # ---------- rich text ----------

    def set_rich_text(self, package: int, name: str = "", handle: int | None = None,
                      text: str = "", paragraphs: list[str] | None = None) -> dict:
        """Write multi-paragraph text into the widget's RichTextPanel.

        Only TextRectObject-derived widgets (shapes, headings, paragraphs) carry a RichTextPanel;
        plain-text widgets (TextBox/TextArea) take a newline string through the normal Text property
        instead. Paragraph breaks are simply newlines inside tSmX.rSma.Text.
        """
        widget = self.resolve_widget(package, name, handle)
        body = text if paragraphs is None else "\n".join(paragraphs)
        if not body:
            raise AxureError("set_rich_text needs text or paragraphs")
        try:
            panel = self.b.get_on(widget, "RichTextPanel")
        except BridgeError:
            self.b.set_on(widget, "Text", body)
            return {"widget": self.widget_info(widget).get("Name"), "paragraphs": body.count("\n") + 1,
                    "mode": "plain-text"}
        rich = self.b.get_on(panel["$h"], "AttrString")
        self.b.set_on(rich["$h"], "Text", body)
        return {"widget": self.widget_info(widget).get("Name"), "paragraphs": body.count("\n") + 1,
                "mode": "rich-text"}

    def rich_text(self, widget: int) -> str:
        panel = self.b.get_on(widget, "RichTextPanel")
        rich = self.b.get_on(panel["$h"], "AttrString")
        return self.b.get_on(rich["$h"], "Text")

    # ---------- tables ----------

    def set_table(self, package: int, name: str = "", handle: int | None = None,
                  rows: int | None = None, columns: int | None = None,
                  data: list[list[str]] | None = None,
                  row_heights: list[float] | None = None,
                  column_widths: list[float] | None = None) -> dict:
        """Resize a table and fill its cells.

        Table is a DiagramObjectContainer: setting Rows/Columns rebuilds the TableCell children, and
        AllTableCells returns them in row-major order, which is what makes cell text cheap to write.
        """
        widget = self.resolve_widget(package, name, handle)
        if rows:
            self.b.set_on(widget, "Rows", int(rows))
        if columns:
            self.b.set_on(widget, "Columns", int(columns))
        if row_heights:
            heights = self.b.call("makedict", key="System.String", value="System.Double")
            self.b.invoke_on(widget, "set_RowHeights",
                             [{"$arr": [float(h) for h in row_heights]}])
        if column_widths:
            self.b.invoke_on(widget, "set_ColumnWidths",
                             [{"$arr": [float(w) for w in column_widths]}])

        written = 0
        if data:
            cells = self.b.get_on(widget, "AllTableCells")
            items = self.b.iter(cells["$h"], limit=2000) or []
            total_columns = int(self.b.get_on(widget, "Columns"))
            for r, row in enumerate(data):
                for c, value in enumerate(row):
                    index = r * total_columns + c
                    if index >= len(items):
                        continue
                    self.b.set_on(items[index]["$h"], "Text", str(value))
                    written += 1
        return {"widget": self.widget_info(widget).get("Name"),
                "rows": self.b.get_on(widget, "Rows"),
                "columns": self.b.get_on(widget, "Columns"), "cells_written": written}

    def same_size(self, package: int, names: list[str], mode: str = "both",
                  reference: str = "first") -> list[dict]:
        """Make widgets the same width / height / both, using the first one as the reference."""
        widgets = [self.resolve_widget(package, n) for n in names]
        if len(widgets) < 2:
            raise AxureError("same_size needs at least two widgets")
        boxes = [self._box(w) for w in widgets]
        base = boxes[0]
        mode = mode.lower()
        out = []
        for widget, box in zip(widgets, boxes):
            width = base["w"] if mode in ("width", "both") else box["w"]
            height = base["h"] if mode in ("height", "both") else box["h"]
            self.set_rect(widget, box["x"], box["y"], width, height)
            out.append({"name": self.widget_info(widget).get("Name"), "width": width, "height": height})
        return out

    # ---------- dynamic panels ----------

    def panel_states(self, package: int, name: str = "", handle: int | None = None) -> dict:
        widget = self.resolve_widget(package, name, handle)
        names = []
        try:
            listing = self.b.get_on(widget, "PanelStateNames")
            names = list(self.b.iter(listing["$h"], limit=200) or [])
        except BridgeError:
            pass
        return {"widget": self.widget_info(widget).get("Name"),
                "count": self.b.get_on(widget, "NumberOfPanelStates"),
                "states": [n for n in names]}

    def add_panel_state(self, package: int, name: str = "", handle: int | None = None,
                        count: int = 1, state_name: str | None = None) -> dict:
        """Append states to a dynamic panel by duplicating an existing one.

        The panel offers no "add state" API - the only route that survives serialisation is the
        edit command `DynamicPanelStatesEdit.DuplicateState(state, index)` followed by Commit().
        (`panel.eNaz(...)` also bumps the count but produces a document that cannot be saved.)
        """
        widget = self.resolve_widget(package, name, handle)
        added = []
        for _ in range(max(1, int(count))):
            states = self.b.call("iter", handle=self.b.get_on(widget, "SubDiagrams")["$h"],
                                 limit=200) or []
            if not states:
                raise AxureError("this panel has no state to duplicate")
            source = states[0]["$h"]
            edit = self.b.call("invoke",
                               type="Axure.DocumentModel.Objects.DynamicPanelStatesEdit",
                               method="DuplicateState", args=[{"$h": source}, 0])
            self.b.invoke_on(edit["$h"], "Commit")
            added.append(True)

        if state_name:
            states = self.b.call("iter", handle=self.b.get_on(widget, "SubDiagrams")["$h"],
                                 limit=200) or []
            if states:
                target = states[-1]["$h"]
                rename = self.b.call("invoke",
                                     type="Axure.DocumentModel.Objects.DynamicPanelStatesEdit",
                                     method="oNaV", args=[{"$h": target}, str(state_name)])
                try:
                    self.b.invoke_on(rename["$h"], "Commit")
                except BridgeError:
                    pass
        return self.panel_states(package, handle=widget)

    # ---------- grouping ----------

    def group(self, package: int, names: list[str], name: str = "group") -> dict:
        """Group widgets into a Layer (Axure's group object).

        Layer is just another KJm7 whose own collection (_DiagramObjects) holds the members, so
        grouping means: create the layer over the members' bounding box, move the members into it,
        and put the layer on the page in their place.
        """
        widgets = [self.resolve_widget(package, n) for n in names]
        if len(widgets) < 2:
            raise AxureError("group needs at least two widgets")
        boxes = [self._box(w) for w in widgets]
        left = min(b["x"] for b in boxes)
        top = min(b["y"] for b in boxes)
        right = max(b["x"] + b["w"] for b in boxes)
        bottom = max(b["y"] + b["h"] for b in boxes)

        # create the layer through the normal insert pipeline, otherwise the page collection
        # rejects it (Add() on a hand-built Layer throws)
        layer = self.add_widget(package, "group", name, None, left, top,
                                right - left, bottom - top)
        for widget in widgets:
            try:
                self.b.invoke_on(self.objects(package), "Remove", [{"$h": widget}])
            except Exception:
                pass
            try:
                self.b.invoke_on(layer, "Add", [{"$h": widget}, 0])
            except Exception:
                pass
        self.set_rect(layer, left, top, right - left, bottom - top)
        return {"name": name, "members": names,
                "bounds": {"x": left, "y": top, "width": right - left, "height": bottom - top}}

    def ungroup(self, package: int, name: str = "", handle: int | None = None) -> dict:
        """Dissolve a group by copying its members back onto the page, then deleting the layer.

        Re-parenting by hand (`layer.Remove(child)` + `pageObjects.Add(child)`) sometimes takes the
        whole process down, so the members are instead re-inserted through the editor pipeline - the
        same path that makes a widget appear when it is dropped from the toolbox - and the layer is
        removed afterwards.
        """
        layer = self.resolve_widget(package, name, handle)
        layer_name = self.widget_info(layer).get("Name")
        children = self.b.get_on(layer, "DiagramObjects")
        members = []
        for item in self.b.iter(children["$h"], limit=500) or []:
            child = item.get("$h") if isinstance(item, dict) else None
            if child:
                members.append(child)

        restored = []
        for child in members:
            info = {}
            try:
                info = self.widget_info(child)
            except BridgeError:
                pass
            box = self._box(child)
            try:
                clone = self._insert_widget(package, child, 0, 0)
                self.set_rect(clone, box["x"], box["y"], box["w"], box["h"])
                if info.get("Name"):
                    self.b.set_on(clone, "Name", info["Name"])
                restored.append(info.get("Name"))
            except Exception:
                restored.append(None)

        try:
            self.b.invoke_on(self.objects(package), "Remove", [{"$h": layer}])
        except Exception:
            pass
        return {"dissolved": layer_name, "members": restored}

    # ---------- history ----------

    def _command(self, name: str) -> int:
        commands = self.b.invoke_on(self.client, "KIX4")
        return self.b.get_on(commands["$h"], name)["$h"]

    def undo(self) -> bool:
        self.b.invoke_on(self._command("Undo"), "RHhc")
        return True

    def redo(self) -> bool:
        self.b.invoke_on(self._command("Redo"), "RHhc")
        return True

    # ---------- content: list items, images ----------

    def set_list_items(self, package: int, name: str = "", handle: int | None = None,
                       items: list[str] | None = None, selected: int | None = None) -> dict:
        """Replace the options of a droplist / listbox.

        The model keeps them as List<otfF.Wtfr>. `Options` is a get/set pair (set_Options exists on
        ListObject), so the cleanest route is to build a fresh list and assign it - mutating the
        existing list in place also works but leaves stale state when the widget was never opened.
        """
        widget = self.resolve_widget(package, name, handle)
        items = list(items or [])
        list_type = ("System.Collections.Generic.List`1[[otfF.Wtfr, AxureRP11, "
                     "Version=11.0.0.4150, Culture=neutral, PublicKeyToken=null]]")
        fresh = self.b.ctor(list_type, [])["$h"]
        for index, label in enumerate(items):
            item = self.b.ctor("otfF.Wtfr", [{"$h": package}, str(label), index == selected])
            self.b.invoke_on(fresh, "Add", [{"$h": item["$h"]}])
        self.b.call("set", handle=widget, name="Options", value={"$h": fresh})

        stored = self.b.call("get", handle=widget, name="Options", ui=False)
        labels = []
        for entry in self.b.iter(stored["$h"], limit=500) or []:
            try:
                labels.append(self.b.invoke_on(entry["$h"], "ltft"))
            except BridgeError:
                labels.append(None)
        return {"widget": self.widget_info(widget).get("Name"), "items": labels,
                "selected": selected}

    def set_image(self, package: int, name: str = "", handle: int | None = None,
                  image_bytes: bytes | None = None, filename: str = "image.png") -> dict:
        """Put an image into an ImageBox. dta1.QtaH is built from a stream + name + format.

        An ImageBox that already carries an image silently keeps the old one when Image is
        reassigned, so the result is verified by comparing the stored byte length.
        """
        widget = self.resolve_widget(package, name, handle)
        if not image_bytes:
            raise AxureError("set_image needs image bytes")
        encoded = base64.b64encode(image_bytes).decode("ascii")
        stream = self.b.ctor("System.IO.MemoryStream", [encoded])
        image = self.b.ctor("dta1.QtaH", [{"$h": stream["$h"]}, filename, "png", False])
        self.b.set_on(widget, "Image", {"$h": image["$h"]})

        stored = self.b.get_on(widget, "Image")
        length = (self.b.dump(stored["$h"], depth=1, limit=6) or {}).get("Length")
        if length != len(image_bytes):
            raise AxureError(
                f"the ImageBox kept its previous image ({length} bytes); Axure only accepts the first "
                "Image assignment - delete and re-create the widget, or use a fresh ImageBox")
        return {"widget": self.widget_info(widget).get("Name"), "bytes": len(image_bytes)}

    # ---------- align / distribute ----------

    def align(self, package: int, names: list[str], mode: str = "left",
              reference: str = "selection") -> list[dict]:
        """Align widgets. mode: left|hcenter|right|top|vcenter|bottom.

        reference="selection" aligns to the artboard (page) edge, "bounds" to the group's bounding box.
        """
        mode = mode.lower()
        widgets = [self.resolve_widget(package, n) for n in names]
        if not widgets:
            raise AxureError("align needs at least one widget")
        boxes = [self._box(w) for w in widgets]

        if reference == "bounds":
            left = min(b["x"] for b in boxes)
            top = min(b["y"] for b in boxes)
            right = max(b["x"] + b["w"] for b in boxes)
            bottom = max(b["y"] + b["h"] for b in boxes)
        else:
            left = top = 0.0
            right = float(self.page_width(package))
            bottom = float(self.page_height(package))

        out = []
        for widget, box in zip(widgets, boxes):
            x, y = box["x"], box["y"]
            if mode == "left":
                x = left
            elif mode == "right":
                x = right - box["w"]
            elif mode == "hcenter":
                x = (left + right) / 2 - box["w"] / 2
            elif mode == "top":
                y = top
            elif mode == "bottom":
                y = bottom - box["h"]
            elif mode == "vcenter":
                y = (top + bottom) / 2 - box["h"] / 2
            else:
                raise AxureError(f"unknown align mode {mode!r}")
            self.set_rect(widget, x, y, box["w"], box["h"])
            out.append({"name": self.widget_info(widget).get("Name"), "x": x, "y": y})
        return out

    def distribute(self, package: int, names: list[str], axis: str = "horizontal",
                   gap: float | None = None) -> list[dict]:
        """Distribute widgets evenly along an axis (equal gaps), keeping their order."""
        widgets = [self.resolve_widget(package, n) for n in names]
        if len(widgets) < 2:
            raise AxureError("distribute needs at least two widgets")
        horizontally = axis.lower().startswith("h")
        boxes = [(w, self._box(w)) for w in widgets]
        boxes.sort(key=lambda item: (item[1]["x"] if horizontally else item[1]["y"]))

        first, last = boxes[0][1], boxes[-1][1]
        if horizontally:
            span = (last["x"] + last["w"]) - first["x"]
            used = sum(box["w"] for _, box in boxes)
        else:
            span = (last["y"] + last["h"]) - first["y"]
            used = sum(box["h"] for _, box in boxes)
        step = gap if gap is not None else max(0.0, (span - used) / (len(boxes) - 1))

        cursor = first["x"] if horizontally else first["y"]
        out = []
        for widget, box in boxes:
            x = cursor if horizontally else box["x"]
            y = box["y"] if horizontally else cursor
            self.set_rect(widget, x, y, box["w"], box["h"])
            out.append({"name": self.widget_info(widget).get("Name"), "x": x, "y": y})
            cursor += (box["w"] if horizontally else box["h"]) + step
        return out

    def _box(self, widget: int) -> dict:
        return {
            "x": float(self.b.get_on(widget, "X") or 0),
            "y": float(self.b.get_on(widget, "Y") or 0),
            "w": float(self.b.get_on(widget, "Width") or 0),
            "h": float(self.b.get_on(widget, "Height") or 0),
        }

    def page_width(self, package: int) -> float:
        return self._page_size(package)[0]

    def page_height(self, package: int) -> float:
        return self._page_size(package)[1]

    def _page_size(self, package: int) -> tuple[float, float]:
        diagram = self.b.get_on(package, "Diagram")
        bounds = self.b.invoke_on(diagram["$h"], "Bounds", [True])
        dump = self.b.dump(bounds["$h"], depth=2, limit=6) or {}
        width = float(dump.get("Width") or 0)
        height = float(dump.get("Height") or 0)
        if width <= 0 or height <= 0:
            width, height = 800.0, 600.0
        return width, height

    # ---------- z-order ----------

    def z_order(self, package: int, names: list[str], action: str = "front") -> list[str]:
        """Reorder widgets. action: front | back.

        Only SendToBack is used: the collection's other move primitives (kJnm/rJ4l/NJ4z) either
        duplicate entries or throw, and Remove+Add drops a widget's internal sub-objects (which is
        how a TextArea silently loses its text). Pushing every *other* widget to the back is the
        same as raising the selected one to the front, without touching its structure.
        """
        objects = self.objects(package)
        action = action.lower()
        handles = [self.resolve_widget(package, n) for n in names]
        if action not in ("front", "back"):
            raise AxureError("z-order supports front and back")

        # NOTE: JX48.mJ4n has no reliable "move to index" primitive. SendToBack is the only one that
        # does not corrupt the collection (kJnm/rJ4l duplicate entries, Remove+Add drops sub-objects
        # such as a TextArea's TextPanel, which silently loses the text). It is applied per widget,
        # so with several names the last one processed wins.
        for handle in handles:
            self.b.invoke_on(objects, "SendToBack", [{"$h": handle}])
        return [self.widget_info(h).get("Name") for h in handles]

    def _position(self, objects: int, widget: int) -> int:
        """Index of a widget inside the DiagramObjects collection."""
        for index, item in enumerate(self.b.iter(objects, limit=4000) or []):
            if isinstance(item, dict) and item.get("$h") == widget:
                return index
        return -1

    def resolve_widget(self, package: int, name: str = "", handle: int | None = None) -> int:
        if handle:
            return handle
        found = self.find_widget(package, name)
        if found is None:
            raise AxureError(f"no widget named {name!r} on this page")
        return found

    def update_widget(self, package: int, name: str = "", handle: int | None = None,
                      text: str | None = None, x: float | None = None, y: float | None = None,
                      width: float | None = None, height: float | None = None,
                      visible: bool | None = None, new_name: str | None = None) -> dict:
        widget = self.resolve_widget(package, name, handle)
        if None not in (x, y, width, height):
            self.set_rect(widget, x, y, width, height)
        else:
            current = {k: self.b.get_on(widget, k) for k in ("X", "Y", "Width", "Height")}
            self.set_rect(widget,
                          x if x is not None else current["X"],
                          y if y is not None else current["Y"],
                          width if width is not None else current["Width"],
                          height if height is not None else current["Height"])
        if text is not None:
            self.b.set_on(widget, "Text", str(text))
        if visible is not None:
            self.b.set_on(widget, "Visible", bool(visible))
        if new_name:
            self.b.set_on(widget, "Name", new_name)
        return self.widget_info(widget)

    def delete_widget(self, package: int, name: str = "", handle: int | None = None) -> bool:
        widget = self.resolve_widget(package, name, handle)
        return bool(self.b.invoke_on(self.objects(package), "Remove", [{"$h": widget}]))

    def clear_widgets(self, package: int) -> int:
        removed = 0
        for item in self.widgets(package):
            if self.b.invoke_on(self.objects(package), "Remove", [{"$h": item["handle"]}]):
                removed += 1
        return removed

    # ---------- render / persist ----------

    def render_page(self, page_id: str, max_dimension: int = 1600) -> bytes:
        payload = self.b.invoke_on(self.service, "GetScreenshot",
                                   [self.document_id, page_id, None, None, int(max_dimension)])
        data = self.b.get_on(payload["$h"], "Data")
        if isinstance(data, str):
            return base64.b64decode(data)
        raw = self.b.iter(data["$h"], limit=8_000_000) or []
        return bytes(raw)

    def commit(self, package: int) -> None:
        """Re-serialize the live package into the .rp container.

        Two steps are needed to make an edit survive a reload: the window's live package has to be
        written back into the document's container store (that is what `stOK` + `gt2C.Save` do,
        gzip-compressed), and only then does Document.Save() rewrite the file.
        """
        b = self.b
        settings = b.invoke_on(self.document, "aNEQ")
        store = b.invoke_on(self.document, "LNEY")

        ctx = b.ctor("etOx.stOK", [{"$h": package}, {"$h": settings["$h"]}])
        b.invoke_on(ctx["$h"], "TtUP", ["SaveAllInternally"])
        b.invoke_on(ctx["$h"], "dtO7")

        raw = b.invoke_on(store["$h"], "Create", [{"$h": package}])
        gz = b.ctor("System.IO.Compression.GZipStream", [{"$h": raw["$h"]}, "Compress"])
        try:
            b.invoke("gt2C", "Save", [{"$h": ctx["$h"]}, {"$h": gz["$h"]}])
        finally:
            for handle in (gz["$h"], raw["$h"]):
                try:
                    b.invoke_on(handle, "Dispose")
                except BridgeError:
                    pass

    def save(self, package: int | None = None, all_pages: bool = False) -> None:
        """Re-serialise pending edits and write the .rp.

        Only the package passed in (or the active one) is committed: Axure keeps one live package per
        page, and walking *every* page during save turned out to damage the document (pages vanished),
        so callers must pass the package they edited - which the MCP handlers do.
        """
        package = package if package is not None else self.active_package()
        if package is None:
            raise AxureError("no page is open in Axure; open one first")
        self.commit(package)
        self.b.invoke_on(self.document, "Save")
        self.flush_paint()

    def flush_paint(self, force: bool = False) -> None:
        """Ask Axure to repaint its canvas.

        The bridge suppresses painting while a mutation runs (otherwise Axure's OnPaint enumerates the
        list we are changing and pops a "Report Error" dialog), so a batch of edits leaves the window
        looking stale until something asks for a repaint. Rate limited: painting is expensive and a
        full repaint per tool call made builds ~6x slower.
        """
        now = time.time()
        if not force and now - getattr(self, "_last_flush", 0.0) < 0.4:
            return
        self._last_flush = now
        try:
            self.b.call("flush_paint")
        except BridgeError:
            pass

    def save_as(self, path: str) -> dict:
        """Write the document out to another .rp path.

        Axure's own FileSaveAs / FileExport commands take no arguments and open modal dialogs, and a
        modal dialog blocks the UI thread the bridge marshals onto (that is what makes Redo hang).
        So this saves in place first and then copies the file - the copy is a valid standalone .rp.
        """
        self.save()
        source = self.file_name()
        if not source or not os.path.exists(source):
            raise AxureError("the document is not backed by a file yet")
        shutil.copy2(source, path)
        return {"source": source, "target": path, "bytes": os.path.getsize(path)}

    def export_image(self, page_id: str | None = None, max_dimension: int = 1400) -> bytes:
        return self.render_page(page_id, max_dimension)

    def close(self) -> None:
        self.b.close()









