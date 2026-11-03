"""Per-version obfuscated symbol tables for the Client.Mac adapter.

Axure rebuilds the internal (obfuscated) member names on every release, so the
adapter cannot bake one set of identifiers in. Instead it keeps one table per
verified app version and selects it by the bridge's reported ``appVersion``.

A table maps a *stable semantic key* to the obfuscated ``Type.Member`` or
member name used on that build. Keys are deliberately descriptive so the
adapter code reads the same on every version.
"""

# Table for Axure RP 11.0.0.4137 (the originally verified Mac build).
SYMBOLS_4137 = {
    # value/helper types
    'color':        'uXeP.xXem',
    'fill':         'vXnb.rXni',
    'rect':         'bXhd.pXh4',
    'folder':       'kb56.rb5r',
    'listitem':     'FV82.kV86',
    'image':        'qVf5.tVf0',
    'path':         'hb8r.nb8K',

    # methods
    'file_name':    'fIed',
    'commit_pkg':   'pIAO',
    'node_payload': 'nbG8',
    'node_name':    'hH42',
    'node_by_guid': 'ybG0',
    'tree_insert':  'kbGu',
    'node_rename':  'AH4r',
    'node_move':    'KbG9',
    'node_detach':  'VbmL',
    'widget_guid':  'wVqm',
    'widget_layer': 'Cb6A',
    'set_bounds':   'FbsR',
    'ann_list':     'tbyA',
    'ann_get':      'yHhE',
    'ix_map':       'GHhq',
    'img_stream':   'DVNA',
    'col_width':    'PIyf',
    'panel_rename': 'WIf4',
    'color_rgb':    'mXen',
    'listitem_lbl': 'KV84',
    'ann_fieldval': 'UbJF',
    'ann_setfield': 'SbJ9',
    'ann_kind':     'pbJ7',
    'ann_store':    'GbOY',
    'rich_clone':   'tAs4',
    'fieldset_get': 'dXWD',
    'field_entries':'cXBB',
    'action_map':   'FIE3',
}

# Table for Axure RP 11.0.0.4149 (Apple Silicon). Resolved by a structural
# diff of the two Client.Mac.dll assemblies and confirmed with live probes.
SYMBOLS_4149 = {
    'color':        'uxrz.ixrF',
    'fill':         'PxVD.FxVu',
    'rect':         'nx9V.Lx9f',
    'folder':       'tHRW.UHRv',
    'listitem':     'Brhc.qrho',
    'image':        'fram.traU',
    'path':         'nHhv.cHhy',

    'file_name':    'cKrc',
    'commit_pkg':   'CKJj',
    'node_payload': 'aHtR',
    'node_name':    'uqhN',
    'node_by_guid': 'GHt7',
    'tree_insert':  'wHtV',
    'node_rename':  'KqhP',
    'node_move':    'RHtt',
    'node_detach':  'DHoJ',
    'widget_guid':  'ErWF',
    'widget_layer': 'jHAC',
    'set_bounds':   'WH18',
    'ann_list':     'DHnC',
    'ann_get':      'oqlW',
    'ix_map':       'fqlj',
    'img_stream':   'wrjN',
    'col_width':    'XKn4',
    'panel_rename': 'oKao',
    'color_rgb':    'CxxH',
    'listitem_lbl': 'Arhf',
    'ann_fieldval': 'cHPF',
    'ann_setfield': 'UHPt',
    'ann_kind':     'gHN0',
    'ann_store':    'GHNE',
    'rich_clone':   'dL12',
    'fieldset_get': 'Ex5x',
    'field_entries':'OxMp',
    'action_map':   'AKws',
}

_TABLES = {
    '11.0.0.4137': SYMBOLS_4137,
    '11.0.0.4149': SYMBOLS_4149,
}


def symbols_for(app_version: str | None) -> dict:
    """Return the symbol table for an Axure appVersion string.

    Exact table match wins. Within a verified ``11.0.0.41xx`` band an unknown
    patch level falls back to the newest table, on the assumption that the
    obfuscation roll is per-release rather than per-patch; the adapter still
    fails closed outside that band.
    """
    if app_version in _TABLES:
        return _TABLES[app_version]
    if isinstance(app_version, str) and app_version.startswith('11.0.0.4149'):
        return SYMBOLS_4149
    if isinstance(app_version, str) and app_version.startswith('11.0.0.41'):
        return SYMBOLS_4149
    raise KeyError(app_version)
