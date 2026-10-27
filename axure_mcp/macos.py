"""Adapter for the independently verified Client.Mac 11.0.0.4137 model.

Internal names are version specific. Keep unsupported tools gated in the server;
never fall back to Windows reflection names for a Mac document.
"""
import base64
import hashlib
import math
import os
import time
import uuid

from .ops import Axure, AxureError, EMPTY_GUID, WIDGET_TYPES
from .client import BridgeError

SUPPORTED_TOOLS = frozenset({
    'axure_status', 'axure_list_pages', 'axure_page_tree', 'axure_get_page',
    'axure_add_widget', 'axure_update_widget', 'axure_delete_widget',
    'axure_align', 'axure_distribute', 'axure_same_size', 'axure_save',
    'axure_save_as', 'axure_open_file', 'axure_create_page', 'axure_rename_page',
    'axure_delete_page', 'axure_move_page', 'axure_style_widget', 'axure_get_widget_style',
    'axure_create_folder', 'axure_set_list_items', 'axure_set_image', 'axure_set_table',
    'axure_set_rich_text', 'axure_panel_states', 'axure_add_panel_state', 'axure_group',
    'axure_ungroup', 'axure_z_order', 'axure_set_page_note', 'axure_get_page_notes',
    'axure_set_widget_note', 'axure_get_widget_notes', 'axure_add_interaction', 'axure_get_interactions',
})

SUPPORTED_WIDGETS = frozenset(WIDGET_TYPES) - {'connector', 'textlink'}
TEXT_WIDGETS = frozenset({'button', 'checkbox', 'radiobutton', 'textbox', 'textarea',
                          'droplist', 'listbox', 'image', 'shape'})


class MacAxure(Axure):
    OPEN_DOCUMENT_TIMEOUT = 20.0

    def _document_key(self, doc):
        # Bridge handles are allocated per response. The native hash remains
        # stable until that document window closes.
        return doc.get('id') or doc.get('fileName')

    def _doc(self):
        docs = self.documents()
        active = [doc for doc in docs
                  if doc.get('isActive') or doc.get('isKeyWindow') or doc.get('isMainWindow')]
        if active:
            # User focus wins over the controller's last-known document. In
            # particular, currentDocument can remain stale while another
            # document window is key.
            selected = max(
                active,
                key=lambda doc: (
                    bool(doc.get('isKeyWindow')),
                    bool(doc.get('isActive')),
                    bool(doc.get('isMainWindow')),
                ),
            )
            self._selected_document = self._document_key(selected)
            return selected
        selected_key = getattr(self, '_selected_document', None)
        for doc in docs:
            if selected_key is not None and self._document_key(doc) == selected_key:
                return doc
        if len(docs) == 1:
            self._selected_document = self._document_key(docs[0])
            return docs[0]
        if not docs:
            raise AxureError('Axure is running but no document is open')
        raise AxureError('cannot identify the active Mac document; select a document window')

    @property
    def client(self):
        # Cocoa allows several independent document windows. Do not keep a
        # cached client after the user selects a different window.
        return self._doc()['handle']

    def file_name(self):
        file = self.b.get_on(self.client, 'CurrentFile')['$h']
        return self.b.invoke_on(file, 'fIed') or ''

    def sitemap(self):
        return self.b.get_on(self.document, 'Sitemap')['$h']

    def page_tree(self):
        sitemap = self.sitemap()
        root = self.b.get_on(sitemap, 'Tree')['$h']

        def walk(node):
            children = self.b.get_on(node, 'Children')['$h']
            out = []
            for item in self.b.iter(children, limit=4000):
                handle = item['$h']
                payload = self.b.invoke_on(sitemap, 'nbG8', [{'$h': handle}])
                is_page = isinstance(payload, str)
                ident = payload if is_page else self.b.get_on(payload['$h'], 'FolderId')
                out.append({'id': ident, 'name': self.b.invoke_on(sitemap, 'hH42', [{'$h': handle}]),
                            'type': 'page' if is_page else 'folder', 'children': walk(handle)})
            return out
        return walk(root)

    def pages(self):
        out = []
        def walk(nodes):
            for node in nodes:
                if node['type'] == 'page':
                    out.append({k: node[k] for k in ('id', 'name', 'type')})
                walk(node['children'])
        walk(self.page_tree())
        return out

    def open_page(self, page_id):
        self.b.invoke_on(self.client, 'OpenOuterObject', [page_id, EMPTY_GUID, False])
        return self.active_package()

    def active_package(self):
        return self.b.get_on(self.client, 'OuterObject')['$h']

    def _find_node(self, page_id):
        # Validate membership before passing an id to reflection.
        def walk(nodes):
            for node in nodes:
                if node['id'] == page_id:
                    return True
                if walk(node['children']):
                    return True
            return False
        if not walk(self.page_tree()):
            raise AxureError(f'no page or folder with id {page_id}')
        return self.b.invoke_on(self.sitemap(), 'ybG0', [{'$guid': page_id}])['$h']

    def create_page(self, name, parent_id=None, index=0):
        sitemap = self.sitemap()
        parent = self._find_node(parent_id) if parent_id else self.b.get_on(sitemap, 'Tree')['$h']
        ident = str(uuid.uuid4())
        node = self.b.invoke_on(sitemap, 'kbGu', [None, {'$h': parent}, index, {'$guid': ident}])['$h']
        self.b.invoke_on(sitemap, 'AH4r', [{'$h': node}, name])
        self.open_page(ident)
        self.save()
        return {'id': ident, 'name': name, 'type': 'page'}

    def rename_page(self, page_id, name):
        self.b.invoke_on(self.sitemap(), 'AH4r', [{'$h': self._find_node(page_id)}, name])
        self.save()

    def _subtree_ids(self, page_id):
        def all_ids(node):
            return {node['id']} | {ident for child in node['children'] for ident in all_ids(child)}
        def find(nodes):
            for node in nodes:
                if node['id'] == page_id:
                    return all_ids(node)
                result = find(node['children'])
                if result is not None:
                    return result
            return None
        return find(self.page_tree()) or set()

    def delete_page(self, page_id):
        pages = self.pages()
        removed = self._subtree_ids(page_id)
        if not removed:
            raise AxureError(f'no page or folder with id {page_id}')
        remaining = [p['id'] for p in pages if p['id'] not in removed]
        if not remaining:
            raise AxureError('cannot delete the last page or a subtree containing all pages')
        node = self._find_node(page_id)
        self.open_page(remaining[0])
        self.b.invoke_on(node, 'VbmL')
        self.save()

    def move_page(self, page_id, parent_id=None, index=0):
        if parent_id in self._subtree_ids(page_id):
            raise AxureError('cannot move a page into itself or one of its descendants')
        self._find_node(page_id)
        sitemap = self.sitemap()
        node = self._find_node(page_id)
        parent = self._find_node(parent_id) if parent_id else self.b.get_on(sitemap, 'Tree')['$h']
        self.b.invoke_on(node, 'VbmL')
        self.b.invoke_on(sitemap, 'KbG9', [{'$h': node}, {'$h': parent}, index])
        self.save()

    def add_widget(self, package, kind, name='', text=None, x=0, y=0, width=120, height=40,
                   extra=None, shape='Rectangle'):
        if kind not in SUPPORTED_WIDGETS:
            raise AxureError(f'{kind} insertion is not supported on this Mac version')
        if text is not None and kind not in TEXT_WIDGETS:
            raise AxureError(f'{kind} does not support text; omit text')
        return super().add_widget(package, kind, name, text, x, y, width, height, extra, shape)

    def _collect_handles(self, objects, deep=False, depth=0):
        # Axure keeps group members in the diagram's global collection. The
        # layer's collection represents membership, not exclusive ownership.
        handles = []
        seen = set()
        for item in self.b.iter(objects, limit=2000) or []:
            handle = item.get('$h') if isinstance(item, dict) else None
            if not handle:
                continue
            try:
                ancestor = self.b.invoke_on(handle, 'Cb6A')
            except BridgeError:
                ancestor = None
            if not deep and ancestor is not None:
                continue
            try:
                ident = self.b.invoke_on(handle, 'wVqm')
            except BridgeError:
                ident = None
            key = ('id', ident) if ident is not None else ('handle', handle)
            if key not in seen:
                seen.add(key)
                handles.append(handle)
            if deep and depth < 4 and item.get('type') == WIDGET_TYPES['group']:
                inner = self.b.get_on(handle, 'DiagramObjects')['$h']
                for child in self._collect_handles(inner, True, depth + 1):
                    try:
                        ident = self.b.invoke_on(child, 'wVqm')
                    except BridgeError:
                        ident = None
                    key = ('id', ident) if ident is not None else ('handle', child)
                    if key not in seen:
                        seen.add(key)
                        handles.append(child)
        return handles

    def resolve_widget(self, package, name='', handle=None):
        if handle is None:
            return super().resolve_widget(package, name, handle)
        # RefreshOuterObject reconstructs the model. A previously returned
        # bridge handle still points at its old object; bind by persistent GUID
        # within the requested page, rather than editing that detached object.
        ident = self.b.invoke_on(handle, 'wVqm')
        for live in self._collect_handles(self.objects(package), deep=True):
            if self.b.invoke_on(live, 'wVqm') == ident:
                return live
        raise AxureError('widget handle is not present on the requested Mac page')

    def _style_handle(self, widget):
        return self.b.get_on(widget, 'MyStyle')['$h']

    def _fill(self, colour):
        raw = colour.lstrip('#')
        if len(raw) != 6:
            raise AxureError('colour must be #RRGGBB')
        try:
            channels = [int(raw[i:i+2], 16) for i in (0, 2, 4)]
        except ValueError as exc:
            raise AxureError('colour must be #RRGGBB') from exc
        rgb = self.b.invoke('uXeP.xXem', 'mXen', channels)['$h']
        return self.b.ctor('vXnb.rXni', [{'$h': rgb}])['$h']

    def _rgb(self, colour_handle):
        return tuple(int(self.b.get_on(colour_handle, key)) for key in ('R', 'G', 'B'))

    def set_rect(self, widget, x, y, width, height):
        rect = self.b.ctor('bXhd.pXh4', [float(x), float(y), float(width), float(height)])
        self.b.invoke_on(widget, 'FbsR', [{'$h': rect['$h']}, True])

    def _insert_widget(self, package, widget, x, y):
        # This model path persists, but does not create an undo entry. Reported
        # as a limitation, rather than claiming equivalence to toolbox insertion.
        self.b.invoke_on(self.objects(package), 'Add', [{'$h': widget}])
        return widget

    def save(self, package=None, all_pages=False):
        if package is not None:
            expected = self.b.get_on(package, 'PackageId')
            active = self.b.get_on(self.active_package(), 'PackageId')
            if active != expected:
                raise AxureError('refusing to save an inactive Mac package')
        self.b.invoke_on(self.client, 'CommitOuterObject', [False])
        file = self.b.get_on(self.client, 'CurrentFile')['$h']
        self.b.invoke_on(file, 'pIAO', [{'$h': package or self.active_package()}, None])
        self.b.invoke_on(file, 'Save')
        self.b.invoke_on(self.client, 'RefreshOuterObject')
        self.b.call('flush_paint')

    def open_file(self, path):
        target = os.path.realpath(os.path.expanduser(str(path)))
        if not target.lower().endswith('.rp'):
            raise AxureError('axure_open_file requires a path ending in .rp')
        if not os.path.isfile(target):
            raise AxureError(f'Axure file does not exist: {target}')
        controller = self.b.invoke(
            'MCocoa.NSDocumentController', 'sharedDocumentController')['$h']
        self.b.invoke_on(controller, 'OpenDocument', [target, False])
        self._svc = self._client = None
        self._selected_document = None
        deadline = time.monotonic() + self.OPEN_DOCUMENT_TIMEOUT
        matches = []
        while True:
            matches = [
                doc for doc in self.documents()
                if os.path.realpath(os.path.expanduser(doc.get('fileName') or '')) == target
            ]
            if matches or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        if not matches:
            raise AxureError('Axure opened the file but no matching document window was found')
        selected = max(
            enumerate(matches),
            key=lambda item: (
                bool(item[1].get('isKeyWindow')),
                bool(item[1].get('isActive')),
                bool(item[1].get('isMainWindow')),
                item[0],
            ),
        )[1]
        self._selected_document = self._document_key(selected)
        return target

    def create_folder(self, name, parent_id=None, index=0):
        sitemap = self.sitemap()
        parent = self._find_node(parent_id) if parent_id else self.b.get_on(sitemap, 'Tree')['$h']
        folder = self.b.ctor('kb56.rb5r', [{'$h': self.document}, name])
        ident = self.b.get_on(folder['$h'], 'FolderId')
        self.b.invoke_on(sitemap, 'kbGu', [None, {'$h': parent}, index, folder])
        self.save()
        return {'id': ident, 'name': name, 'type': 'folder'}

    def set_list_items(self, package, name='', handle=None, items=None, selected=None):
        items = list(items or [])
        if selected is not None and not 0 <= selected < len(items):
            raise AxureError('selected index must identify an item in the supplied list')
        widget = self.resolve_widget(package, name, handle)
        if self.b.identity(widget)['type'] not in {
                WIDGET_TYPES['droplist'], WIDGET_TYPES['listbox']}:
            raise AxureError('set_list_items requires a droplist or listbox')
        fresh = self.b.ctor('System.Collections.Generic.List`1[[FV82.kV86, Client.Mac]]')['$h']
        for index, label in enumerate(items):
            item = self.b.ctor('FV82.kV86', [{'$h': package}, label, index == selected])
            self.b.invoke_on(fresh, 'Add', [item])
        self.b.set_on(widget, 'Options', {'$h': fresh})
        return {'widget': self.widget_info(widget).get('Name'), 'items': items, 'selected': selected}

    def image_bytes(self, widget):
        image = self.b.get_on(widget, 'Image')
        if not image:
            return b''
        try:
            length = self.b.get_on(image['$h'], 'Length')
        except BridgeError as exc:
            # An ImageBox created without an asset has an image object whose
            # length property dereferences a missing backing stream.
            if 'NullReferenceException' in str(exc):
                return b''
            raise
        if not length:
            return b''
        stream = self.b.invoke_on(image['$h'], 'DVNA')['$h']
        return base64.b64decode(self.b.invoke_on(stream, 'ToArray'))

    def set_image(self, package, name='', handle=None, image_bytes=None, filename='image.png'):
        if not image_bytes:
            raise AxureError('set_image needs image bytes')
        widget = self.resolve_widget(package, name, handle)
        if self.b.identity(widget)['type'] != WIDGET_TYPES['image']:
            raise AxureError('set_image requires an image widget')
        stream = self.b.ctor('System.IO.MemoryStream', [base64.b64encode(image_bytes).decode('ascii')])
        # The third string is a cache key, NOT an image format. Passing "png"
        # aliases every image to the first asset, including same-length replacements.
        image = self.b.ctor('qVf5.tVf0', [stream, filename, None, False])
        self.b.set_on(widget, 'Image', image)
        if self.image_bytes(widget) != image_bytes:
            raise AxureError('Axure did not retain the supplied image bytes')
        return {'widget': self.widget_info(widget).get('Name'), 'bytes': len(image_bytes),
                'sha256': hashlib.sha256(image_bytes).hexdigest()}

    def set_table(self, package, name='', handle=None, rows=None, columns=None,
                  data=None, row_heights=None, column_widths=None):
        # Check all dimensions before touching the table, including optional arrays.
        if data:
            rows = len(data) if rows is None else rows
            columns = max(map(len, data)) if columns is None else columns
        if rows is not None and (type(rows) is not int or rows < 1):
            raise AxureError('rows must be a positive integer')
        if columns is not None and (type(columns) is not int or columns < 1):
            raise AxureError('columns must be a positive integer')
        widget = self.resolve_widget(package, name, handle)
        if self.b.identity(widget)['type'] != WIDGET_TYPES['table']:
            raise AxureError('set_table requires a table widget')
        original_rows = self.b.get_on(widget, 'Rows')
        original_columns = self.b.get_on(widget, 'Columns')
        rows = original_rows if rows is None else rows
        columns = original_columns if columns is None else columns
        if rows < 1 or columns < 1 or rows * columns > 10000:
            raise AxureError('table dimensions must be positive and at most 10000 cells')
        if data and (len(data) > rows or any(len(row) > columns for row in data)):
            raise AxureError('data exceeds the requested table dimensions')
        for label, values, count in [('row_heights', row_heights, rows),
                                      ('column_widths', column_widths, columns)]:
            if values is not None and (
                    len(values) != count
                    or any(isinstance(v, bool) or not isinstance(v, (int, float))
                           or not math.isfinite(v) or v <= 0 for v in values)):
                raise AxureError(f'{label} must contain {count} positive dimensions')
        if data and any(not isinstance(row, list) for row in data):
            raise AxureError('table data must be a two-dimensional array')
        if data and any(not isinstance(text, str) for row in data for text in row):
            raise AxureError('table cell values must be strings')
        dimensions_changed = rows != original_rows or columns != original_columns
        if dimensions_changed:
            # On Client.Mac, setting Rows/Columns rebuilds AllTableCells. Resolve
            # the cells only after resizing, and restore the old dimensions if
            # the native model fails to produce the complete new collection.
            try:
                if rows != original_rows:
                    self.b.set_on(widget, 'Rows', rows)
                if columns != original_columns:
                    self.b.set_on(widget, 'Columns', columns)
                cells = self.b.iter(self.b.get_on(widget, 'AllTableCells')['$h'], limit=10000)
                if len(cells) < rows * columns:
                    raise AxureError(
                        'table cell collection is smaller than the requested dimensions')
            except (AxureError, BridgeError) as exc:
                try:
                    if rows != original_rows:
                        self.b.set_on(widget, 'Rows', original_rows)
                    if columns != original_columns:
                        self.b.set_on(widget, 'Columns', original_columns)
                except Exception as rollback_exc:  # noqa: BLE001 - preserve both failures
                    raise AxureError(
                        f'table resize failed ({exc}) and rollback failed ({rollback_exc})') from exc
                raise
        else:
            cells = self.b.iter(self.b.get_on(widget, 'AllTableCells')['$h'], limit=10000)
            if len(cells) < rows * columns:
                raise AxureError(
                    'table cell collection is smaller than the requested dimensions')
        if row_heights is not None:
            self.b.invoke_on(widget, 'set_RowHeights', [{'$arr': row_heights}])
        if column_widths is not None:
            for index, width in enumerate(column_widths):
                self.b.invoke_on(widget, 'PIyf', [index, float(width), False])
        for r, row in enumerate(data or []):
            for c, text in enumerate(row):
                self.b.set_on(cells[r * columns + c]['$h'], 'Text', text)
        return {'widget': self.widget_info(widget).get('Name'), 'rows': rows, 'columns': columns,
                'cells_written': sum(map(len, data or []))}

    def add_panel_state(self, package, name='', handle=None, count=1, state_name=None):
        if not 1 <= count <= 100:
            raise AxureError('count must be between 1 and 100')
        widget = self.resolve_widget(package, name, handle)
        states = self.b.iter(self.b.get_on(widget, 'SubDiagrams')['$h'], limit=1000)
        if not states:
            raise AxureError('this panel has no state to duplicate')
        for _ in range(count):
            source = self.b.iter(self.b.get_on(widget, 'SubDiagrams')['$h'], limit=1000)[-1]['$h']
            edit = self.b.invoke('Axure.DocumentModel.Objects.DynamicPanelStatesEdit',
                                 'DuplicateState', [{'$h': source}, 0])
            self.b.invoke_on(edit['$h'], 'Commit')
        if state_name:
            target = self.b.iter(self.b.get_on(widget, 'SubDiagrams')['$h'], limit=1000)[-1]['$h']
            edit = self.b.invoke('Axure.DocumentModel.Objects.DynamicPanelStatesEdit',
                                 'WIf4', [{'$h': target}, state_name])
            if edit:
                self.b.invoke_on(edit['$h'], 'Commit')
        result = self.panel_states(package, handle=widget)
        if result['count'] != len(states) + count:
            raise AxureError('Axure did not retain every requested panel state')
        if state_name and result['states'][-1] != state_name:
            raise AxureError('Axure did not apply the requested panel state name')
        return result

    def _top_widgets(self, package, names):
        if len(names) != len(set(names)):
            raise AxureError('widget names must be unique in this operation')
        handles = [self.resolve_widget(package, name) for name in names]
        top = {self.b.invoke_on(h, 'wVqm') for h in self._collect_handles(self.objects(package))}
        if any(self.b.invoke_on(h, 'wVqm') not in top for h in handles):
            raise AxureError('operation requires top-level widgets on this page')
        return handles

    def group(self, package, names, name='group'):
        if len(names) < 2:
            raise AxureError('group needs at least two widgets')
        widgets = self._top_widgets(package, names)
        if self.find_widget(package, name) is not None:
            raise AxureError('group name already exists')
        layer = self.add_widget(package, 'group', name)
        objects = self.objects(package)
        moved = []
        try:
            for widget in widgets:
                self.b.invoke_on(layer, 'Add', [{'$h': widget}, len(moved) - 1])
                moved.append(widget)
            box = self._box(layer)
        except BridgeError as exc:
            # Members remain in the page's global collection while grouped; rollback
            # therefore removes membership and the temporary layer, never re-adds members.
            rollback_failed = False
            for widget in reversed(moved):
                try:
                    self.b.invoke_on(layer, 'Remove', [{'$h': widget}])
                except BridgeError:
                    rollback_failed = True
            try:
                self.b.invoke_on(objects, 'Remove', [{'$h': layer}])
            except BridgeError:
                rollback_failed = True
            if rollback_failed:
                raise AxureError(
                    'group failed and Axure could not fully roll back; inspect the page before retrying'
                ) from exc
            raise
        return {'name': name, 'members': names,
                'bounds': {'x': box['x'], 'y': box['y'], 'width': box['w'], 'height': box['h']}}

    def ungroup(self, package, name='', handle=None):
        layer = self.resolve_widget(package, name, handle)
        if self.b.identity(layer)['type'] != WIDGET_TYPES['group']:
            raise AxureError('ungroup requires a group widget')
        children = self.b.iter(self.b.get_on(layer, 'DiagramObjects')['$h'], limit=2000)
        names = [self.b.get_on(child['$h'], 'Name') for child in children]
        objects = self.objects(package)
        for child in children:
            self.b.invoke_on(layer, 'Remove', [child])
        self.b.invoke_on(objects, 'Remove', [{'$h': layer}])
        return {'dissolved': name or self.widget_info(layer).get('Name'), 'members': names}

    def z_order(self, package, names, action='front'):
        if action not in {'front', 'back'}:
            raise AxureError('z-order supports front and back')
        selected = {self.b.invoke_on(h, 'wVqm') for h in self._top_widgets(package, names)}
        objects = self.objects(package)
        ordered = self._collect_handles(objects)
        # Sending the complement backwards raises a selection without removing,
        # cloning or damaging rich text / nested objects. Preserve relative order.
        for widget in reversed(ordered):
            chosen = self.b.invoke_on(widget, 'wVqm') in selected
            if chosen == (action == 'back'):
                self.b.invoke_on(objects, 'SendToBack', [{'$h': widget}])
        return names

    def _note_fields(self, widget=False):
        prop = 'DiagramObjectAnnotationFieldSet' if widget else 'PageFragmentAnnotationFieldSet'
        holder = self.b.get_on(self.document, prop)['$h']
        fields = self.b.invoke_on(holder, 'dXWD')['$h']
        entries = self.b.iter(self.b.invoke_on(fields, 'cXBB')['$h'], limit=100)
        return [{'name': self.b.get_on(e['$h'], 'Name'),
                 'id': self.b.get_on(e['$h'], 'PropertyNameId')} for e in entries]

    def _read_notes(self, annotation, fields):
        # Read only stored fields. Getter fallbacks return a shared default rich
        # string, which must never be mistaken for a note actually set here.
        store = self.b.get_on(annotation, 'GbOY')['$h']
        raw = {}
        for item in self.b.iter(store, limit=100):
            key = self.b.get_on(item['$h'], 'Key')
            rich = self.b.get_on(item['$h'], 'Value')['$h']
            raw[key] = self.b.get_on(rich, 'Text')
        return {field['name']: raw.get(field['id'], '') for field in fields}

    def _write_note(self, annotation, fields, text, field):
        target = next((f for f in fields if field is None or f['name'] == field), None)
        if target is None:
            raise AxureError(f'unknown note field {field!r}; available: {[f["name"] for f in fields]}')
        original = self.b.invoke_on(annotation, 'UbJF', [{'$guid': target['id']}])['$h']
        # Mutating original aliases the shared empty template across page and
        # widget notes. Clone the attributed string before modifying it.
        rich = self.b.invoke_on(original, 'tAs4')['$h']
        self.b.set_on(rich, 'Text', text)
        self.b.invoke_on(annotation, 'SbJ9', [target['name'], {'$h': rich}])
        return {'field': target['name'], 'text': text, 'empty': not bool(text)}

    def page_notes(self, package):
        annotation = self.b.get_on(package, 'Annotation')['$h']
        return self._read_notes(annotation, self._note_fields())

    def set_page_note(self, package, text, field=None):
        annotation = self.b.get_on(package, 'Annotation')['$h']
        return self._write_note(annotation, self._note_fields(), text, field)

    def set_widget_note(self, package, name='', handle=None, text='', field=None):
        widget = self.resolve_widget(package, name, handle)
        fields = self._note_fields(True)
        if field is not None and field not in {f['name'] for f in fields}:
            raise AxureError(f'unknown widget note field {field!r}')
        entries = self.b.iter(self.b.invoke_on(widget, 'tbyA')['$h'], limit=100)
        annotation = entries[0]['$h'] if entries else self.b.invoke_on(widget, 'yHhE')['$h']
        result = self._write_note(annotation, fields, text, field)
        return {'widget': self.widget_info(widget).get('Name'), 'field': result['field'], 'note': text}

    def widget_notes(self, package):
        fields = self._note_fields(True)
        result = []
        for widget in self._collect_handles(self.objects(package), deep=True):
            entries = self.b.iter(self.b.invoke_on(widget, 'tbyA')['$h'], limit=100)
            for entry in entries:
                annotation = entry['$h']
                result.append({'owner': self.b.get_on(annotation, 'OwnerId'),
                               'kind': self.b.invoke_on(annotation, 'pbJ7'),
                               'fields': self._read_notes(annotation, fields)})
        return result

    def widget_content(self, widget):
        kind = self.b.identity(widget)['type']
        if kind in {WIDGET_TYPES['droplist'], WIDGET_TYPES['listbox']}:
            entries = self.b.iter(self.b.get_on(widget, 'Options')['$h'], limit=2000)
            return {'kind': 'list', 'items': [self.b.invoke_on(e['$h'], 'KV84') for e in entries],
                    'selected': [i for i, e in enumerate(entries) if self.b.get_on(e['$h'], 'Selected')]}
        if kind == WIDGET_TYPES['image']:
            raw = self.image_bytes(widget)
            return {
                'kind': 'image',
                'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest() if raw else None,
            }
        if kind == WIDGET_TYPES['table']:
            rows, columns = (self.b.get_on(widget, key) for key in ('Rows', 'Columns'))
            cells = self.b.iter(self.b.get_on(widget, 'AllTableCells')['$h'], limit=10000)
            texts = [self.b.get_on(cell['$h'], 'Text') for cell in cells]
            return {'kind': 'table', 'rows': rows, 'columns': columns,
                    'data': [texts[i * columns:(i + 1) * columns] for i in range(rows)],
                    'row_heights': self.b.iter(self.b.get_on(widget, 'RowHeights')['$h']),
                    'column_widths': self.b.iter(self.b.get_on(widget, 'ColumnWidths')['$h'])}
        if kind == WIDGET_TYPES['group']:
            members = self.b.iter(self.b.get_on(widget, 'DiagramObjects')['$h'], limit=2000)
            return {'kind': 'group', 'members': [self.b.get_on(m['$h'], 'Name') for m in members]}
        if kind == WIDGET_TYPES['dynamicpanel']:
            return {'kind': 'panel', 'count': self.b.get_on(widget, 'NumberOfPanelStates'),
                    'states': self.b.iter(self.b.get_on(widget, 'PanelStateNames')['$h'])}
        return None
    def _ix_map(self, widget):
        return self.b.invoke_on(widget, 'GHhq')['$h']

    def _ix_children(self, node):
        return self.b.iter(self.b.get_on(node, 'Children')['$h'], limit=1000)

    def interactions(self, widget):
        ix = self._ix_map(widget)
        root = self.b.get_on(ix, 'Tree')['$h']
        result = []
        for event_node in self._ix_children(root):
            ev = self.b.invoke_on(ix, 'nbG8', [event_node])['$h']
            record = {'type': self.b.get_on(ev, 'EventType'), 'cases': []}
            for case_node in self._ix_children(event_node['$h']):
                case = self.b.invoke_on(ix, 'nbG8', [case_node])['$h']
                entry = {'description': self.b.get_on(case, 'Description'), 'actions': []}
                for action_node in self._ix_children(case_node['$h']):
                    action = self.b.invoke_on(ix, 'nbG8', [action_node])['$h']
                    info = {'TypeName': self.b.get_on(action, 'TypeName'),
                            'EditorText': self.b.get_on(action, 'EditorText')}
                    if 'WaitAction' in self.b.identity(action)['type']:
                        info['WaitTime'] = self.b.get_on(action, 'WaitTime')
                    entry['actions'].append(info)
                record['cases'].append(entry)
            result.append(record)
        return result

    def _mac_action(self, package, spec):
        if not isinstance(spec, dict):
            raise AxureError('each interaction action must be an object')
        action = spec.get('action', 'navigate')
        if not isinstance(action, str):
            raise AxureError('interaction action must be a string')
        action = action.lower()
        allowed = {
            'navigate': {'action', 'target_page_id', 'link_type'},
            'close': {'action'},
            'wait': {'action', 'wait_ms'},
            'set_opacity': {'action', 'target_widget', 'value'},
            'move': {'action', 'target_widget', 'value', 'mode'},
            'rotate': {'action', 'target_widget', 'value', 'mode'},
            'resize': {'action', 'target_widget', 'value'},
        }
        if action not in allowed:
            raise AxureError(f'action {action!r} is not supported on Mac')
        unknown = set(spec) - allowed[action]
        if unknown:
            raise AxureError(f'unknown action field(s): {", ".join(sorted(unknown))}')
        if action == 'navigate':
            target = spec.get('target_page_id')
            if target not in {p['id'] for p in self.pages()}:
                raise AxureError('navigate requires an existing target_page_id')
            link_type = spec.get('link_type') or 'Current'
            if link_type not in {'Current', 'New', 'Popup', 'Parent'}:
                raise AxureError('navigate link_type must be Current, New, Popup or Parent')
            result = self.b.ctor('Axure.DocumentModel.Interaction.IxLinkWindowAction',
                                 [{'$h': package}, link_type])['$h']
            destination = self.b.get_on(result, 'Target')['$h']
            self.b.set_on(destination, 'TargetType', 'Page')
            self.b.set_on(destination, 'TargetPagePackageId', {'$guid': target})
            return result
        if action == 'close':
            return self.b.ctor('Axure.DocumentModel.Interaction.IxCloseCurrentAction', [{'$h': package}])['$h']
        if action == 'wait':
            wait = spec.get('wait_ms')
            wait = 500 if wait is None else wait
            if type(wait) is not int or wait < 0:
                raise AxureError('wait_ms must be a nonnegative integer')
            result = self.b.ctor('Axure.DocumentModel.Interaction.IxWaitAction', [{'$h': package}])['$h']
            self.b.set_on(result, 'WaitTime', wait)
            return result
        classes = {'set_opacity': ('IxSetOpacityAction', 'SetOpacityInfo'),
                   'move': ('IxMoveWidgetAction', 'MoveWidgetInfo'),
                   'rotate': ('IxRotateWidgetAction', 'RotateWidgetInfo'),
                   'resize': ('IxSetWidgetSizeAction', 'SetWidgetSizeInfo')}
        if not spec.get('target_widget'):
            raise AxureError(f'{action} requires target_widget')
        if not isinstance(spec['target_widget'], str):
            raise AxureError('target_widget must be a widget name')
        target = self.resolve_widget(package, spec['target_widget'])
        value = spec.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise AxureError(f'{action} requires a finite numeric value')
        if action == 'set_opacity' and not 0 <= value <= 1:
            raise AxureError('set_opacity value must be between 0 and 1')
        if action == 'resize' and value <= 0:
            raise AxureError('resize factor must be positive')
        mode = spec.get('mode') or 'Location'
        aliases = {'to':'Location', 'by':'Delta', 'location':'Location', 'delta':'Delta'}
        if action in {'move', 'rotate'}:
            if not isinstance(mode, str):
                raise AxureError('move/rotate mode must be Location or Delta (To/By)')
            mode = aliases.get(mode.lower())
            if mode is None:
                raise AxureError('move/rotate mode must be Location or Delta (To/By)')
        cls, info_class = classes[action]
        durations = self.b.call('makedict', key='System.String', value='System.Double')
        result = self.b.ctor('Axure.DocumentModel.Interaction.' + cls, [{'$h': package}, durations])['$h']
        path = self.b.ctor('hb8r.nb8K', [{'$h': package}, None, None, {'$guid': self.b.invoke_on(target, 'wVqm')}])
        if action == 'set_opacity':
            args = [float(value) * 100, 0.0]
        elif action == 'move':
            args = [0.0, mode]
        elif action == 'rotate':
            args = [0.0]
        else:
            box = self._box(target)
            args = [box['w'] * value, box['h'] * value, 0.0]
        info = self.b.ctor('Axure.DocumentModel.Interaction.' + info_class, [{'$h': package}, *args])['$h']
        if action == 'move':
            for key in ('XValue', 'YValue'):
                literal = self.b.get_on(info, key)['$h']
                self.b.set_on(literal, 'RawValue', str(value))
        if action == 'rotate':
            self.b.set_on(info, 'RotateType', mode)
            self.b.set_on(self.b.get_on(info, 'DegreeLiteral')['$h'], 'RawValue', str(value))
        mapping = self.b.get_on(result, 'FIE3')['$h']
        self.b.invoke_on(mapping, 'Add', [path, {'$h': info}])
        return result

    def add_interaction(self, package, name='', handle=None, event='OnClick', action='navigate',
                        target_page_id=None, link_type='Current', wait_ms=None,
                        target_widget=None, fade=None, value=None, mode=None):
        if not isinstance(action, str):
            raise AxureError('interaction action must be a string')
        action = action.lower()
        spec = {'action': action}
        if action == 'navigate':
            spec.update(target_page_id=target_page_id, link_type=link_type)
        elif action == 'wait':
            spec['wait_ms'] = wait_ms
        elif action in {'set_opacity', 'move', 'rotate', 'resize'}:
            spec.update(target_widget=target_widget, value=value)
            if action in {'move', 'rotate'}:
                spec['mode'] = mode
        return self.add_actions(package, name=name, handle=handle, event=event, actions=[spec])

    def add_actions(self, package, name='', handle=None, event='OnClick', actions=None):
        actions = list(actions or [])
        if not actions:
            raise AxureError('add_actions needs at least one action')
        widget = self.resolve_widget(package, name, handle)
        supported = self.b.iter(self.b.get_on(widget, 'SupportedIxEventTypes')['$h'], limit=200)
        if event not in supported:
            raise AxureError(f'event {event!r} is not supported by this widget')
        # Build and validate the entire batch before adding any event/case nodes.
        event_object = self.b.ctor('Axure.DocumentModel.Interaction.IxEvent', [{'$h': package}, event])
        built = [self._mac_action(package, spec) for spec in actions]
        ix = self._ix_map(widget)
        root = self.b.get_on(ix, 'Tree')['$h']
        event_node = None
        case_node = None
        created_event = False
        try:
            for node in self._ix_children(root):
                obj = self.b.invoke_on(ix, 'nbG8', [node])['$h']
                if self.b.get_on(obj, 'EventType') == event:
                    event_node = node
                    break
            if event_node is None:
                event_node = self.b.invoke_on(
                    ix, 'kbGu',
                    [None, {'$h': root}, len(self._ix_children(root)), event_object])
                created_event = True
            case = self.b.ctor('Axure.DocumentModel.Interaction.IxCase', [{'$h': package}])
            case_node = self.b.invoke_on(
                ix, 'kbGu',
                [None, event_node, len(self._ix_children(event_node['$h'])), case])
            for index, action in enumerate(built):
                self.b.invoke_on(ix, 'kbGu', [None, case_node, index, {'$h': action}])
        except BridgeError:
            if case_node is not None:
                try:
                    self.b.invoke_on(case_node['$h'], 'VbmL')
                except BridgeError:
                    pass
            if created_event:
                try:
                    self.b.invoke_on(event_node['$h'], 'VbmL')
                except BridgeError:
                    pass
            raise
        return {'widget': self.widget_info(widget).get('Name'), 'event': event,
                'actions': len(built), 'interactions': self.interactions(widget)}
