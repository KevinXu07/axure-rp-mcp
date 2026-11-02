# Verification report

Generated: 2026-11-03 03:33
File: test/verify.rp (same file before and after the restart)

Rule: an item passes only if it can be read back after save -> kill -> relaunch.

## Phase B - survives save + restart

| check | result | note |
| --- | --- | --- |
| page_tree | PASS | flat=['PageDelta', 'PageAlpha', 'PageBeta', 'Page 1'] |
| move_page | PASS | folder present, moved page alive |
| widgets | PASS | 9 top-level widgets, group=True |
| update_widget | PASS | Title updated |
| style | PASS | {'Fill': '#2F6FED', 'CornerRadius': 8, 'Bold': 'TRUE'} |
| interaction | PASS | ['Axure:InteractionSetOpacityAction', 'Axure:InteractionSetPanelStateA |
| ix_targets | PASS | fade=['Disable', 'Hide'] state=['2'] opacity=['40'] |
| group_edit | PASS | grp2 members after add/remove: ['btn2', 'btn4'] |
| page_style | PASS | {'page': 'PageAlpha', 'fill': '#F5F0E6', 'opacity': 1.0} |
| page_note | PASS | ['regression page note'] |
| widget_note | PASS | [{'Note': 'button note text'}] |
| widget_note_a | PASS | page note and widget note coexist; ['btn'] |
| copy_widget | PASS | {'Name': 'btnD_copy', 'Text': 'Delta', 'X': 80, 'Y': 160} |
| image_bytes | PASS | 204 -> 204 |
| panel_states | PASS | ['State 1', 'stateTwo'] |
| image | PASS | img widget present |
| table | PASS | tbl widget present |
| render | PASS | png ok |
| save_as | PASS | copy exists |

Phase B: 19/19

## Phase A - write calls accepted

| step | result | note |
| --- | --- | --- |
| add_btn | PASS |  |
| add_btn2 | PASS |  |
| add_btn3 | PASS |  |
| add_btn4 | PASS |  |
| add_btn_delta | PASS |  |
| add_dd | PASS |  |
| add_img | PASS |  |
| add_panel | PASS |  |
| add_rich | PASS |  |
| add_state | PASS |  |
| add_tbl | PASS |  |
| add_txt | PASS |  |
| align | PASS |  |
| copy_widget | PASS |  |
| create_folder | PASS |  |
| create_page_a | PASS |  |
| create_page_b | PASS |  |
| create_page_c | PASS |  |
| create_page_d | PASS |  |
| delete_page | PASS |  |
| distribute | PASS |  |
| get_image | PASS |  |
| get_interactions | PASS |  |
| get_page_notes | PASS |  |
| get_style | PASS |  |
| get_widget_notes | PASS |  |
| group | PASS |  |
| group_add | PASS |  |
| group_remove | PASS |  |
| interaction | PASS |  |
| ix_disable | PASS |  |
| ix_hide | PASS |  |
| ix_opacity | PASS |  |
| ix_panel_state | PASS |  |
| list_pages | PASS |  |
| move_page | PASS |  |
| page_note | PASS |  |
| page_style | PASS |  |
| page_tree | PASS |  |
| panel_states | PASS |  |
| regroup | PASS |  |
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
| widget_note | PASS |  |
| widget_note_a | PASS |  |
| z_order | PASS |  |

Phase A: 55