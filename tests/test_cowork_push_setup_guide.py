"""推送前的两类设置题（总表 CW-D-MS、CW-D-SG，`assistant.guided.push_setup`）：MR.ERP 导入菜单 / 主档默认值、Express 存货科目组。

不连库：替身 frappe 与连接服务（`connections.service`），要问哪几项用真的 `pearnly_core.mrerp.needs`。断言的是应有状态：
- MR.ERP 账套：只问这个方向用到、还要人选的菜单与默认值，选项是读到的可选值；菜单还没读到时 ``known`` 为假；
- Express 账套：只在记存货、读到两个以上合格组、还没选过时问科目组；服务没读到过时 ``known`` 为假；记费用不问；
- 作答：只收此刻问的项、值在选项里，否则整批拒、一项都不记；记法走连接服务现成的 choose_*；菜单 / 默认值记下后推一次公司页刷新。

    python3 tests/test_cowork_push_setup_guide.py
"""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_cowork"):
    sys.path.insert(0, str(ROOT / "apps" / app))

frappe = types.ModuleType("frappe")
frappe._ = lambda text: text
frappe.ValidationError = ValueError


def _throw(message, exc=ValueError):
    raise exc(message)


frappe.throw = _throw
sys.modules["frappe"] = frappe

STATE = {}


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _menu_view(connection, key):
    return STATE["menu_view"]


_module("pearnly_cowork.connections")
_module("pearnly_cowork.connections.service", adapter_of=lambda connection: STATE["adapters"][connection],
        mrerp_menu=_menu_view, stock_acc_group=lambda connection, key: STATE["groups"],
        choose_mrerp_menu=lambda c, k, kind, value: STATE["saved"].append(("menu", kind, value)),
        choose_mrerp_default=lambda c, k, field, value: STATE["saved"].append(("default", field, value)),
        choose_stock_acc_group=lambda c, k, value: STATE["saved"].append(("group", value)))
_module("pearnly_cowork.assistant.tools", announce_mrerp_choices=lambda c, k: STATE["announced"].append((c, k)))

from pearnly_core.mrerp import needs  # noqa: E402
from pearnly_cowork.assistant.guided import push_setup  # noqa: E402


def _view():
    """一份 MR.ERP 菜单视图：采购方向用到的每一类都要人选，各给两项；主档默认值每个字段要选、给两项。"""
    kinds, fields = needs.used("purchase")
    return {"known": True,
            "kinds": {kind: {"needed": True, "options": [{"idmenu": f"{kind}-1", "label": "Menu 1"}, {"idmenu": f"{kind}-2"}]}
                      for kind in kinds},
            "defaults": {"known": True, "fields": {field: {"needed": True, "options": [{"code": "D1", "name": "Dept"},
                                                                                          {"code": "D2"}]}
                                                   for field in fields}}}


class Base(unittest.TestCase):
    def setUp(self):
        STATE.clear()
        STATE.update(adapters={"M": "mrerp", "E": "express"}, menu_view=_view(), saved=[], announced=[],
                     groups={"known": True, "chosen": None, "groups": [{"acccod": "1510", "label": "1510 · Goods"},
                                                                       {"acccod": "1520", "label": "1520 · Parts"}]})


class Mrerp(Base):
    def test_only_the_menus_and_defaults_this_direction_still_needs_are_asked(self):
        found = push_setup.items("M", "MR1", "purchase")
        kinds, fields = needs.used("purchase")
        self.assertTrue(found["known"])
        self.assertEqual([(item["kind"], item["subject"]) for item in found["items"]],
                         [("mrerp_menu", kind) for kind in kinds] + [("mrerp_default", field) for field in fields])
        menu = found["items"][0]
        self.assertEqual(menu["options"], [{"code": f"{kinds[0]}-1", "name": "Menu 1"}, {"code": f"{kinds[0]}-2", "name": f"{kinds[0]}-2"}])
        if fields:
            self.assertEqual(found["items"][len(kinds)]["options"][0], {"code": "D1", "name": "D1 · Dept"})

    def test_menus_not_read_yet_are_not_known(self):
        STATE["menu_view"] = {"known": False, "kinds": {}, "defaults": {}}
        self.assertEqual(push_setup.items("M", "MR1", "purchase"), {"known": False, "items": []})

    def test_answers_are_saved_through_the_connection_and_the_company_page_refreshes(self):
        kinds, _fields = needs.used("purchase")
        saved = push_setup.remember("M", "MR1", "purchase", [{"kind": "mrerp_menu", "subject": kinds[0], "value": f"{kinds[0]}-2"}])
        self.assertEqual((saved, STATE["saved"], STATE["announced"]), (1, [("menu", kinds[0], f"{kinds[0]}-2")], [("M", "MR1")]))

    def test_a_value_not_offered_or_an_item_not_asked_refuses_the_whole_batch(self):
        kinds, _fields = needs.used("purchase")
        good = {"kind": "mrerp_menu", "subject": kinds[0], "value": f"{kinds[0]}-1"}
        for bad in ({"kind": "mrerp_menu", "subject": kinds[0], "value": "X"},
                    {"kind": "mrerp_menu", "subject": "not_asked", "value": "X"},
                    {"kind": "stock_group", "subject": "", "value": "1510"}):
            with self.assertRaises(ValueError):
                push_setup.remember("M", "MR1", "purchase", [good, bad])
        self.assertEqual(STATE["saved"], [])


class StockGroup(Base):
    def test_asked_only_for_stock_with_two_groups_not_chosen(self):
        found = push_setup.items("E", "2026", "purchase", stock=True)
        self.assertEqual([(item["kind"], [o["code"] for o in item["options"]]) for item in found["items"]],
                         [("stock_group", ["1510", "1520"])])
        self.assertEqual(push_setup.items("E", "2026", "purchase"), {"known": True, "items": []})   # 记费用不问
        STATE["groups"]["chosen"] = {"acccod": "1510"}
        self.assertEqual(push_setup.items("E", "2026", "purchase", stock=True)["items"], [])
        STATE["groups"] = {"known": False, "groups": []}
        self.assertFalse(push_setup.items("E", "2026", "purchase", stock=True)["known"])

    def test_choosing_a_group_saves_it_without_refreshing_mrerp_choices(self):
        push_setup.remember("E", "2026", "purchase", [{"kind": "stock_group", "subject": "", "value": "1520"}], stock=True)
        self.assertEqual((STATE["saved"], STATE["announced"]), ([("group", "1520")], []))


if __name__ == "__main__":
    unittest.main()
