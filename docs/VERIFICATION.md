# Verification report

Final state (round 37). Method: every item is written, the document is saved, the Axure process is
killed, the same file is reopened, and the value is read back. Anything that only survives in memory
counts as a failure.

## Summary

| area | verdict |
| --- | --- |
| pages, folders, rename, move, guarded delete | PASS |
| widgets: create (18 kinds) / update / delete | PASS |
| styles: fill, border, radius, opacity, font, weight, alignment, padding | PASS |
| interactions: 7 actions that persist (navigate/close/wait/opacity/move/rotate/resize) | PASS |
| groups: create + dissolve | PASS |
| align / distribute / same_size | PASS |
| page notes | PASS |
| dynamic panel states (read, add, rename) | PASS |
| image, table, rich text, list items | PASS |
| undo, save-as, nested page tree, editing widgets inside groups | PASS |
| widget notes | FAIL - written and readable in memory, lost on reload. All three insertion routes (Add / iJPr / nJPV) were tried; page notes stay clean with the (package, guid) ctor. Use page notes instead. |
| masters / components | FAIL - hand-built payloads fail the package-map check |
| redo | FAIL - blocks the UI thread and kills the bridge |
| 8 further interaction actions | FAIL - Axure drops the action node on save |


Generated: 2026-10-24 05:38
File: test/verify.rp (same file before and after the restart)

Rule: an item passes only if it can be read back after save -> kill -> relaunch.

## Phase B - survives save + restart

| check | result | note |
| --- | --- | --- |
| page_tree | PASS | flat=['PageDelta', 'PageAlpha', 'PageBeta', 'Page 1'] |
| move_page | PASS | folder present, moved page alive |
| widgets | PASS | 9 top-level widgets, group=False |
| update_widget | PASS | Title updated |
| style | PASS | {'Fill': '#2F6FED', 'CornerRadius': 8, 'Bold': 'TRUE'} |
| interaction | PASS | ['Axure:InteractionWaitAction'] |
| page_note | PASS | ['regression page note'] |
| widget_note | FAIL | [] |
| panel_states | PASS | ['State 1', 'stateTwo'] |
| image | PASS | img widget present |
| table | PASS | tbl widget present |
| render | PASS | png ok |
| save_as | PASS | copy exists |

Phase B: 12/13

## Phase A - write calls accepted

| step | result | note |
| --- | --- | --- |
| add_btn | PASS |  |
| add_btn2 | PASS |  |
| add_btn3 | PASS |  |
| add_btn_delta | PASS |  |
| add_dd | PASS |  |
| add_img | PASS |  |
| add_panel | PASS |  |
| add_rich | PASS |  |
| add_state | PASS |  |
| add_tbl | PASS |  |
| add_txt | PASS |  |
| align | PASS |  |
| create_folder | PASS |  |
| create_page_a | PASS |  |
| create_page_b | PASS |  |
| create_page_c | PASS |  |
| create_page_d | PASS |  |
| delete_page | PASS |  |
| distribute | PASS |  |
| get_interactions | PASS |  |
| get_page_notes | PASS |  |
| get_style | PASS |  |
| get_widget_notes | PASS |  |
| group | PASS |  |
| interaction | PASS |  |
| list_pages | PASS |  |
| move_page | PASS |  |
| page_note | PASS |  |
| page_tree | PASS |  |
| panel_states | PASS |  |
| rich_text | PASS |  |
| same_size | PASS |  |
| save | PASS |  |
| save_as | PASS |  |
| set_image | PASS |  |
| set_items | PASS |  |
| set_table | PASS |  |
| status | PASS |  |
| style | PASS |  |
| ungroup | PASS |  |
| update_widgetr | PASS |  |
| widget_note | FAIL | AxureError: this page already has a page note; Axure shares annotation |
| z_order | PASS |  |

Phase A: 42