"""站外 LINE 表面随部署同步（pearnly_core.line_surfaces + 三个产品的富菜单模块）。

11-26 事故：ris 的 DMS 富菜单 9 月开户后从没随部署更新。部署的 surfaces 步骤逐站对照、有漂移才发布、发布后回读，
回读仍不一致就失败；没有渠道凭据的站点跳过并列出。

Run with Frappe installed (candidate image). No Site, database, LINE, Redis or network:
LINE, the lock store, the hooks and the site config are stand-ins.
"""
import importlib
import json
import unittest
from unittest.mock import MagicMock, patch

import frappe

from pearnly_core import line_surfaces
from pearnly_core.line_surfaces import __main__ as runner

# 产品 → hooks 声明的表面模块；ERP / Cowork 的发布仍在原菜单模块（PUBLISHER），表面模块只读对照与回退
MODULES = {"erp": "pearnly_erp_portal.integrations.line_menu_surface", "cowork": "pearnly_cowork.line.menu_surface",
           "dms": "pearnly_dms.menu.rich_menu"}
PUBLISHER = {"erp": "pearnly_erp_portal.integrations.line_menu", "cowork": "pearnly_cowork.line.menu",
             "dms": "pearnly_dms.menu.rich_menu"}


class Channel:
    """一个 Messaging API 频道的替身：菜单列表、默认菜单、图。``accept=False`` 时建菜单不落到列表里（发布没生效）。"""

    def __init__(self, accept=True):
        self.menus, self.images, self.default, self.accept, self.created = [], set(), "", accept, 0
        self.bodies, self.pictures, self.user_menus = {}, {}, {}

    def request(self, method, url, *, session=None, **kwargs):
        path = url.split("/v2/bot/", 1)[1]
        if path == "richmenu/list":
            return Response({"richmenus": list(self.menus)})
        if method == "POST" and path == "richmenu":
            self.created += 1
            menu_id = f"richmenu-new-{self.created}"
            if self.accept:
                self.menus.append({"richMenuId": menu_id, "name": kwargs["json"]["name"]})
                self.bodies[menu_id] = dict(kwargs["json"])
            return Response({"richMenuId": menu_id})
        if path.endswith("/content"):
            menu_id = path.split("/")[1]
            if method == "POST":
                self.images.add(menu_id)
                self.pictures[menu_id] = kwargs.get("data")
                return Response({})
            return Response({}, status=200 if menu_id in self.images else 404, content=self.pictures.get(menu_id))
        if method == "GET" and path.startswith("richmenu/") and path.count("/") == 1:
            menu_id = path.split("/")[1]
            return Response(self.bodies.get(menu_id, {}), status=200 if menu_id in self.bodies else 404)
        if path.startswith("user/U") and path.split("/")[1] in getattr(self, "rejected", ()):
            response = Response({}, status=400)
            response.text = "{\"message\":\"The value for the 'userId' parameter is invalid\"}"
            return response
        if path.startswith("user/U") and path.endswith("/richmenu") and method == "GET":
            menu_id = self.user_menus.get(path.split("/")[1])
            return Response({"richMenuId": menu_id} if menu_id else {}, status=200 if menu_id else 404)
        if path == "user/all/richmenu":
            return Response({"richMenuId": self.default} if self.default else {}, status=200 if self.default else 404)
        if method == "POST" and path.startswith("user/all/richmenu/"):
            self.default = path.rsplit("/", 1)[1]
        return Response({})

    def seed(self, module, names: dict):
        """把 ``names``（键 → 菜单名）作为已发布的菜单放上频道（body 与图取 ``module.release``），返回对应的站点配置。"""
        config = {}
        for index, (k, name) in enumerate(sorted(names.items())):
            menu_id = f"richmenu-old-{index}"
            self.menus.append({"richMenuId": menu_id, "name": name})
            self.images.add(menu_id)
            self.bodies[menu_id], self.pictures[menu_id] = module.release(k)
            config[k] = menu_id
        if module.__name__.endswith("rich_menu"):
            config["basic"], config["query"] = config["th:basic"], config["th:query"]
            self.default = config["basic"]
        return config


class Response:
    def __init__(self, body, status=200, content=None):
        self.status_code, self._body, self.text = status, body, ""
        self.content = content if content is not None else (b"{}" if body else b"")

    def close(self):
        pass

    def json(self):
        return self._body


class Store:
    def make_key(self, key):
        return key

    def lock(self, key, *, timeout, blocking_timeout):
        return Lock()


class Lock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Surfaces(unittest.TestCase):
    def setUp(self):
        self.conf = {"pearnly_line_liff_id": "1234-abcd"}
        self.reconciled = []
        # 有效绑定的人 → 他该挂哪个键（替身的 remount 照这个挂当前配置里的编号）；remount_works=False 模拟重挂没生效
        self.bindings, self.remount_works = {}, True

    def stage(self, product, channel, *, can_send=True, version="v2", image_version=None):
        image_version = image_version or version
        surface = importlib.import_module(MODULES[product])
        module = importlib.import_module(PUBLISHER[product])
        store = Store()
        conf = self.conf

        def update_site_config(key, value, *a, **k):
            conf[key] = value

        stack = [
            patch.object(module.line, "can_send", lambda: can_send),
            patch.object(module.line, "request", channel.request),
            patch.object(module, "reconcile", lambda: self.reconciled.append(product)),
            patch("frappe.installer.update_site_config", update_site_config),
            patch.object(frappe, "conf", conf, create=True),
            patch.object(frappe, "db", MagicMock(), create=True),
            patch.object(frappe.local, "site", f"test{product}.test", create=True),
            patch.object(frappe, "get_hooks", lambda name: [{"product": p, "module": m} for p, m in MODULES.items()]
                         if name == line_surfaces.HOOK else []),
            # 内容与图不是这里要测的：换成按参数（和「代码版本」）区分的小替身
            patch.object(module, "payload", lambda *args: {"chatBarText": "menu " + version, "areas": [
                {"bounds": {"x": 0, "y": 0, "width": 833, "height": 843},
                 "action": {"type": "postback", "label": f"{args!r} {version}", "data": "a=1"}}]}),
            patch.object(module, "_image", lambda *args: (repr(args) + image_version).encode()),
        ]
        stack.append(patch.object(module, "_state_store", lambda: store) if hasattr(module, "_state_store")
                     else patch.object(module.pearnly_state, "store", lambda: store))
        if hasattr(surface, "_IMAGES"):
            stack.append(patch.dict(surface._IMAGES, {}, clear=True))
        if surface is not module:
            stack.append(patch.object(surface.line, "can_send", lambda: can_send))

        def remount(line_user_id):
            if line_user_id in getattr(channel, "rejected", ()):
                raise RuntimeError("LINE rich menu HTTP 400: The value for the 'userId' parameter is invalid")
            key = self.bindings[line_user_id]
            if not self.remount_works:
                return
            if key:
                channel.user_menus[line_user_id] = conf[surface.CONFIG][key]
            else:
                channel.user_menus.pop(line_user_id, None)

        stack += [patch.object(surface, "remount", remount),
                  patch.object(surface, "wants_menu", lambda line_user_id: bool(self.bindings.get(line_user_id))),
                  patch.object(frappe, "get_all", lambda doctype, filters=None, pluck=None, **k: list(self.bindings)
                               if doctype == "Pearnly LINE Binding" and filters == {"status": "active"} else [])]
        for p in stack:
            p.start()
            self.addCleanup(p.stop)
        return surface

    def published_old(self, product, channel, image_version="v1"):
        """频道上挂着上一版（v1）的菜单，站点配置指着它们。"""
        old = self.stage(product, channel, version="v1", image_version=image_version)
        config = channel.seed(old, old.wanted())
        patch.stopall()
        self.conf[old.CONFIG] = config

    def test_no_drift_means_no_publish(self):
        for product in MODULES:
            with self.subTest(product):
                channel = Channel()
                module = self.stage(product, channel)
                self.conf[module.CONFIG] = channel.seed(module, module.wanted())
                rows = line_surfaces.sync_site(product, emit=lambda text: self.fail("no BEFORE without drift"))
                self.assertEqual(rows["rich_menu"]["published"], False)
                self.assertTrue(rows["rich_menu"]["ok"])
                self.assertEqual(channel.created, 0)
                patch.stopall()

    def test_drift_publishes_logs_before_and_reads_back(self):
        for product in MODULES:
            with self.subTest(product):
                channel, lines = Channel(), []
                self.published_old(product, channel)
                module = self.stage(product, channel)
                before = dict(self.conf[module.CONFIG])
                self.assertFalse(line_surfaces.clean(module.drift()))
                rows = line_surfaces.sync_site(product, emit=lines.append)
                row = rows["rich_menu"]
                self.assertTrue(row["published"] and row["ok"])
                self.assertTrue(row["was"]["behind"])
                self.assertEqual(json.loads(lines[1].removeprefix("BEFORE "))["config"], before)
                self.assertTrue(line_surfaces.clean(module.drift()))
                self.assertIn(product, self.reconciled)
                if product == "dms":
                    self.assertEqual(channel.default, self.conf[module.CONFIG]["th:basic"])
                patch.stopall()
                self.reconciled.clear()

    def test_plan_says_which_text_changed_and_whether_the_image_did(self):
        """部署前与部署日志的 PLAN：文字字段逐项列出旧 → 新，图逐字节比（11-28 Cowork 12 套：只改了文字、图不变）。"""
        for product in MODULES:
            with self.subTest(product):
                channel, lines = Channel(), []
                self.published_old(product, channel, image_version="same")
                module = self.stage(product, channel, image_version="same")
                changes = line_surfaces.check_site(product)["rich_menu"]["changes"]
                self.assertEqual(set(changes), set(module.drift()["behind"]))
                one = changes[sorted(changes)[0]]
                self.assertEqual(one["image"], "same")
                self.assertIn("chatBarText: menu v1 -> menu v2", one["text"])
                self.assertTrue(any(t.startswith("cell 1 label: ") and t.endswith(" v2") for t in one["text"]), one["text"])
                line_surfaces.sync_site(product, emit=lines.append)
                plan = json.loads(lines[0].removeprefix("PLAN "))
                self.assertTrue(lines[0].startswith("PLAN ") and lines[1].startswith("BEFORE "))
                self.assertEqual(plan["changes"], changes)
                patch.stopall()
                channel, lines = Channel(), []
                self.published_old(product, channel)
                self.stage(product, channel)
                changes = line_surfaces.check_site(product)["rich_menu"]["changes"]
                self.assertEqual({row["image"] for row in changes.values()}, {"changed"})
                patch.stopall()

    def test_bound_users_are_remounted_and_read_back(self):
        """发布后当场重挂：挂着个人菜单的人数与发布前一致、每个人都挂到这一版；没生效就失败（部署失败）。"""
        for product in MODULES:
            with self.subTest(product):
                channel = Channel()
                self.published_old(product, channel)
                module = self.stage(product, channel)
                keys = sorted(k for k in self.conf[module.CONFIG] if ":" in k)
                old = dict(self.conf[module.CONFIG])
                # carol 没权限、本来就没挂；dave 以前漏挂，这次补上
                self.bindings = {"Ualice": keys[0], "Ubob": keys[-1], "Ucarol": None, "Udave": keys[0]}
                channel.user_menus = {"Ualice": old[keys[0]], "Ubob": old[keys[-1]]}
                self.remount_works = True
                row = line_surfaces.sync_site(product, emit=lambda text: None)["rich_menu"]
                self.assertEqual((row["mounted_before"], row["mounted_after"]), (2, 3))
                new = self.conf[module.CONFIG]
                self.assertEqual(channel.user_menus, {"Ualice": new[keys[0]], "Ubob": new[keys[-1]], "Udave": new[keys[0]]})
                self.assertNotEqual(new[keys[0]], old[keys[0]])
                patch.stopall()
                # 发布前挂着、按规则已不该有（权限收回后旧菜单没摘）：摘掉是对的，只计数
                channel = Channel()
                self.published_old(product, channel)
                module = self.stage(product, channel)
                self.bindings = {"Ualice": None}
                channel.user_menus = {"Ualice": dict(self.conf[module.CONFIG])[keys[0]]}
                row = line_surfaces.sync_site(product, emit=lambda text: None)["rich_menu"]
                self.assertEqual((row["mounted_after"], row["removed_by_rules"]), (0, 1))
                patch.stopall()
                # 该有却没挂上（重挂没生效、被摘了）→ 失败
                channel = Channel()
                self.published_old(product, channel)
                module = self.stage(product, channel)
                self.bindings = {"Ualice": keys[0]}
                channel.user_menus = {"Ualice": dict(self.conf[module.CONFIG])[keys[0]]}
                with patch.object(module, "remount", lambda uid: channel.user_menus.pop(uid, None)):
                    with self.assertRaisesRegex(RuntimeError, "lost: \\['Ualice'\\]"):
                        line_surfaces.sync_site(product, emit=lambda text: None)
                patch.stopall()

    def test_user_ids_line_rejects_are_skipped_not_a_failure(self):
        """部署回读留下的假身份：LINE 回 400 userId。跳过并计数，不让整站（整次部署）失败；别的错照样失败。"""
        for product in MODULES:
            with self.subTest(product):
                channel = Channel()
                self.published_old(product, channel)
                module = self.stage(product, channel)
                key = sorted(k for k in self.conf[module.CONFIG] if ":" in k)[0]
                self.bindings = {"Ualice": key, "Ufake": key}
                channel.user_menus = {"Ualice": self.conf[module.CONFIG][key]}
                channel.rejected = {"Ufake"}
                row = line_surfaces.sync_site(product, emit=lambda text: None)["rich_menu"]
                self.assertEqual((row["mounted_before"], row["mounted_after"], row["rejected_ids"]), (1, 1, 1))
                patch.stopall()

    def test_remount_that_leaves_users_on_old_menus_fails(self):
        for product in MODULES:
            with self.subTest(product):
                channel = Channel()
                self.published_old(product, channel)
                module = self.stage(product, channel)
                key = sorted(k for k in self.conf[module.CONFIG] if ":" in k)[0]
                self.bindings = {"Ualice": key}
                channel.user_menus = {"Ualice": self.conf[module.CONFIG][key]}
                self.remount_works = False
                with self.assertRaisesRegex(RuntimeError, "remount readback: 1 users had a personal menu before, 1 after; "
                                                          "lost: \\[\\]; not on this release: \\['Ualice'\\]"):
                    line_surfaces.sync_site(product, emit=lambda text: None)
                patch.stopall()

    def test_never_published_is_left_to_onboarding(self):
        """开户没发布过的站点部署不抢先发（DMS 会换官方号默认菜单）；缺几个键（加了语言 / 组合）照常发。"""
        for product in MODULES:
            with self.subTest(product):
                channel = Channel()
                module = self.stage(product, channel)
                self.conf.pop(module.CONFIG, None)
                self.assertEqual(len(module.drift()["missing"]), len(module.wanted()))
                rows = line_surfaces.sync_site(product, emit=lambda text: None)
                self.assertEqual(rows, {"rich_menu": {"skip": line_surfaces.NEVER_PUBLISHED}})
                self.assertEqual(channel.created, 0)
                config = channel.seed(module, module.wanted())
                config.pop(sorted(k for k in config if ":" in k)[0])
                self.conf[module.CONFIG] = config
                self.assertTrue(line_surfaces.sync_site(product, emit=lambda text: None)["rich_menu"]["published"])
                patch.stopall()

    def test_publish_that_does_not_land_fails_the_deploy(self):
        for product in MODULES:
            with self.subTest(product):
                channel = Channel()
                self.published_old(product, channel)
                channel.accept = False
                self.stage(product, channel)
                with self.assertRaisesRegex(RuntimeError, "still differs after publish"):
                    line_surfaces.sync_site(product, emit=lambda text: None)
                patch.stopall()

    def test_sites_without_credentials_are_skipped_with_a_reason(self):
        for product in MODULES:
            with self.subTest(product):
                channel = Channel()
                self.stage(product, channel, can_send=False)
                rows = line_surfaces.sync_site(product, emit=lambda text: None)
                self.assertEqual(rows, {"rich_menu": {"skip": "no LINE channel credentials"}})
                self.assertEqual(channel.created, 0)
                patch.stopall()

    def test_dms_without_liff_is_skipped(self):
        self.conf.pop("pearnly_line_liff_id")
        self.stage("dms", Channel())
        self.assertEqual(line_surfaces.check_site("dms"), {"rich_menu": {"skip": "no LIFF"}})

    def test_dms_default_menu_is_part_of_the_drift(self):
        channel = Channel()
        module = self.stage("dms", channel)
        self.conf[module.CONFIG] = channel.seed(module, module.wanted())
        channel.default = "richmenu-someone-else"
        self.assertIs(module.drift()["default"], False)
        line_surfaces.sync_site("dms", emit=lambda text: None)
        self.assertIs(module.drift()["default"], True)

    def test_only_the_sites_own_product_is_used(self):
        self.stage("cowork", Channel())
        modules = line_surfaces.declared("cowork")
        self.assertEqual([m.__name__ for m in modules], [MODULES["cowork"]])

    def test_restore_puts_back_the_before_config(self):
        for product in MODULES:
            with self.subTest(product):
                channel = Channel()
                self.published_old(product, channel)
                module = self.stage(product, channel)
                before = dict(self.conf[module.CONFIG])
                line_surfaces.sync_site(product, emit=lambda text: None)
                self.assertNotEqual(self.conf[module.CONFIG], before)
                line_surfaces.restore_site(product, "rich_menu", before)
                self.assertEqual(self.conf[module.CONFIG], before)
                if product == "dms":
                    self.assertEqual(channel.default, before["basic"])
                patch.stopall()


class FakeFrappe:
    """``runner`` 里 ``frappe`` 的替身：每站一份配置，``connect`` 可以按站失败。"""

    def __init__(self, sites):
        self.sites, self.conf, self.site, self.db = sites, None, None, MagicMock()

    def init(self, site, sites_path):
        self.site, self.conf = site, self.sites[site]

    def connect(self):
        if self.conf.get("__broken"):
            raise RuntimeError("cannot connect")

    def destroy(self):
        self.site = self.conf = None


class Runner(unittest.TestCase):
    """``python -m pearnly_core.line_surfaces``：逐站跑、出错的站点不挡后面的站、有漂移 / 出错退出码 1、跳过的照列。"""

    def run_main(self, sites, mode="check"):
        fake, lines, self.visited = FakeFrappe(sites), [], []

        def rows(product, **kwargs):
            self.visited.append(fake.site)
            if fake.conf.get("__raise"):
                raise RuntimeError(fake.conf["__raise"])
            return fake.conf["__rows"]

        with patch.object(runner, "frappe", fake), patch.object(runner.os, "listdir", lambda path: list(sites)), \
                patch.object(runner.os.path, "isfile", lambda path: True), \
                patch.object(runner.line_surfaces, "check_site", rows), patch.object(runner.line_surfaces, "sync_site", rows), \
                patch("builtins.print", lambda text, **k: lines.append(text)):
            code = runner.main([mode, "--product", "cowork"])
        self.lines = lines
        summary = [line for line in lines if line.startswith("SUMMARY ")]
        return code, json.loads(summary[0].removeprefix("SUMMARY "))

    def test_test_tenants_go_first_and_a_failed_sync_leaves_the_formal_tenants_alone(self):
        """11-28 主会话：testcowork 的发布是 erpcowork 之前的真实演练——测试站先，任一站不过就停，后面的正式站不动。"""
        ok = {"rich_menu": {"behind": [], "missing": [], "ok": True, "published": True}}
        sites = {"erpcowork.pearnly.com": {"pearnly_product": "cowork", "__rows": ok},
                 "acme.pearnly.com": {"pearnly_product": "cowork", "__rows": ok},
                 "testcowork.pearnly.com": {"pearnly_product": "cowork", "__raise": "remount readback: lost"}}
        code, summary = self.run_main(sites, mode="sync")
        self.assertEqual(code, 1)
        self.assertEqual(self.visited, ["testcowork.pearnly.com"])
        self.assertEqual(summary["not_run"], ["acme.pearnly.com", "erpcowork.pearnly.com"])
        self.assertTrue(any(line.startswith("ROLLBACK testcowork.pearnly.com: ") and "restore --product cowork" in line
                            for line in self.lines))
        sites["testcowork.pearnly.com"] = {"pearnly_product": "cowork", "__rows": ok}
        code, summary = self.run_main(sites, mode="sync")
        self.assertEqual((code, summary["not_run"]), (0, []))
        self.assertEqual(self.visited, ["testcowork.pearnly.com", "acme.pearnly.com", "erpcowork.pearnly.com"])

    def test_check_still_reads_every_site_after_a_failure(self):
        ok = {"rich_menu": {"behind": [], "missing": [], "ok": True}}
        code, summary = self.run_main({"testcowork.pearnly.com": {"pearnly_product": "cowork", "__raise": "x"},
                                       "erpcowork.pearnly.com": {"pearnly_product": "cowork", "__rows": ok}})
        self.assertEqual(self.visited, ["testcowork.pearnly.com", "erpcowork.pearnly.com"])
        self.assertEqual((list(summary["failed"]), summary["not_run"]), (["testcowork.pearnly.com"], []))

    def test_drift_skips_and_failures_are_reported(self):
        code, summary = self.run_main({
            "a.test": {"pearnly_product": "cowork", "__rows": {"rich_menu": {"behind": ["en:intake"], "missing": [], "ok": False}}},
            "b.test": {"pearnly_product": "cowork", "__rows": {"rich_menu": {"skip": "no LINE channel credentials"}}},
            "c.test": {"pearnly_product": "erp", "__rows": {"rich_menu": {"ok": False}}},
            "d.test": {"pearnly_product": "cowork", "__broken": True},
            "e.test": {"pearnly_product": "cowork", "__rows": {"rich_menu": {"behind": [], "missing": [], "ok": True}}},
            "f.test": {"pearnly_product": "cowork", "__rows": {}},
        })
        self.assertEqual(code, 1)
        self.assertEqual(summary["drift"], ["a.test rich_menu"])
        self.assertEqual(summary["skipped"], {"b.test rich_menu": "no LINE channel credentials"})
        self.assertEqual(list(summary["failed"]), ["d.test", "f.test"])
        self.assertEqual(summary["ok"], ["e.test rich_menu"])

    def test_all_clean_exits_zero(self):
        code, summary = self.run_main({
            "a.test": {"pearnly_product": "cowork", "__rows": {"rich_menu": {"behind": [], "missing": [], "ok": True}}},
        })
        self.assertEqual((code, summary["failed"], summary["drift"]), (0, {}, []))


if __name__ == "__main__":
    unittest.main()
