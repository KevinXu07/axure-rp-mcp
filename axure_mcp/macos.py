"""Adapter for the independently verified Client.Mac 11.0.0.4137 model.

Internal names are version specific. Keep unsupported tools gated in the server;
never fall back to Windows reflection names for a Mac document.
"""
import uuid

from .ops import Axure, AxureError, EMPTY_GUID, WIDGET_TYPES

SUPPORTED_TOOLS = frozenset({
    'axure_status', 'axure_list_pages', 'axure_page_tree', 'axure_get_page',
    'axure_add_widget', 'axure_update_widget', 'axure_delete_widget',
    'axure_align', 'axure_distribute', 'axure_same_size', 'axure_save',
    'axure_save_as', 'axure_open_file', 'axure_create_page', 'axure_rename_page',
    'axure_delete_page', 'axure_move_page', 'axure_style_widget', 'axure_get_widget_style',
})

SUPPORTED_WIDGETS = frozenset(WIDGET_TYPES) - {'connector', 'textlink'}
TEXT_WIDGETS = frozenset({'button', 'checkbox', 'radiobutton', 'textbox', 'textarea',
                          'droplist', 'listbox', 'image', 'shape'})


class MacAxure(Axure):
    def _doc(self):
        docs = self.documents()
        for doc in docs:
            if doc.get('isActive'):
                return doc
        if len(docs) == 1:
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
        if page_id not in {p['id'] for p in pages}:
            raise AxureError('Mac adapter only deletes pages, not folders')
        removed = self._subtree_ids(page_id)
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
        if page_id not in {p['id'] for p in self.pages()}:
            raise AxureError('Mac adapter only moves pages, not folders')
        sitemap = self.sitemap()
        node = self._find_node(page_id)
        parent = self._find_node(parent_id) if parent_id else self.b.get_on(sitemap, 'Tree')['$h']
        self.b.invoke_on(node, 'VbmL')
        self.b.invoke_on(sitemap, 'KbG9', [{'$h': node}, {'$h': parent}, index])
        self.save()

    def add_widget(self, package, kind, name='', text=None, **kwargs):
        if kind not in SUPPORTED_WIDGETS:
            raise AxureError(f'{kind} insertion is not supported on this Mac version')
        if text is not None and kind not in TEXT_WIDGETS:
            raise AxureError(f'{kind} does not support text; omit text')
        return super().add_widget(package, kind, name, text, **kwargs)

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

    def open_file(self, path):
        self.b.invoke_on(self.client, 'OpenDocumentInSameAXDocument', [str(path), False])
        self._client = self._svc = None
        return self.file_name()
