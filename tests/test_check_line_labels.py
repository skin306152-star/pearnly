# -*- coding: utf-8 -*-
"""LINE 按钮文字闸门（scripts/check-line-labels.py）：认得出各种写法的按钮词条，卡片字段名不算按钮。"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_line_labels", ROOT / "scripts" / "check-line-labels.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

ERP = '''
DIRECTION_LABELS = {"purchase": "Purchase", "sales": "Sales"}

def _button(label, data):
    label = str(label)[:20]
    return {"type": "action", "action": {"type": "postback", "label": label, "data": data}}

def _kv(label, value):
    return {"type": "box", "contents": [{"type": "text", "text": label}, {"type": "text", "text": value}]}

def ask():
    items = [_button(_(DIRECTION_LABELS[d]), "a=mode:" + d) for d in DIRECTION_LABELS]
    items.append(_button(_("Cancel"), "a=cancel"))
    card = [_kv(_("WHT amount"), "-")]
    return {"type": "uri", "label": _("Edit")[:20], "uri": "x"}
'''

DMS = '''
_STATUS_CHOICES = {"sales": (("ST_MANAGER_PENDING", "m"), ("ST_DONE", "d"))}

def _t(key, **kw):
    return i18n.t(f"dms.card.query.{key}", lang=i18n.line_lang(), **kw)

def _item(label, action, **data):
    return {"type": "action", "action": {"type": "postback", "label": label, "data": action}}

def status(kind):
    return [_item(_t(key), "q") for key, value in _STATUS_CHOICES.get(kind, ())]

def menu():
    for num, title in (("3", cards.TXT_MENU_ITEM3), ("4", cards.TXT_MENU_ITEM4)):
        account_action("credentials", title)
'''

LINKS = '''
def account_action(action, label):
    return {"type": "uri", "label": label, "uri": "x"}
'''


class Scan(unittest.TestCase):
    def test_finds_every_way_a_button_label_is_written(self):
        params = gate.label_params([ERP, DMS, LINKS])
        self.assertEqual(params, {"_button": 0, "_item": 0, "account_action": 1})
        erp = {(kind, value) for _, kind, value in gate.scan("apps/pearnly_erp_portal/x/line_flow.py", ERP, params)}
        self.assertEqual(erp, {("en", "Purchase"), ("en", "Sales"), ("en", "Cancel"), ("en", "Edit")})
        self.assertNotIn(("en", "WHT amount"), erp)  # 卡片字段名不是按钮
        dms = {(kind, value) for _, kind, value in gate.scan("apps/pearnly_dms/pearnly_dms/query/q.py", DMS, params)}
        self.assertEqual(dms, {("key", "dms.card.query.ST_MANAGER_PENDING"), ("key", "dms.card.query.ST_DONE"),
                               ("key", "dms.card.TXT_MENU_ITEM3"), ("key", "dms.card.TXT_MENU_ITEM4")})

    def test_the_repository_has_no_label_over_twenty_characters(self):
        self.assertEqual(gate.main([]), 0)


if __name__ == "__main__":
    unittest.main()
