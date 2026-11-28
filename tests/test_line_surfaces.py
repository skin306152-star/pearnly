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

MODULES = {"erp": "pearnly_erp_portal.integrations.line_menu", "cowork": "pearnly_cowork.line.menu",
           "dms": "pearnly_dms.menu.rich_menu"}


class Channel:
    """一个 Messaging API 频道的替身：菜单列表、默认菜单、图。``accept=False`` 时建菜单不落到列表里（发布没生效）。"""

    def __init__(self, accept=True):
        self.menus, self.images, self.default, self.accept, self.created = [], set(), "", accept, 0

    def request(self, method, url, *, session=None, **kwargs):
        path = url.split("/v2/bot/", 1)[1]
        if path == "richmenu/list":
            return Response({"richmenus": list(self.menus)})
        if method == "POST" and path == "richmenu":
            self.created += 1
            menu_id = f"richmenu-new-{self.created}"
            if self.accept:
                self.menus.append({"richMenuId": menu_id, "name": kwargs["json"]["name"]})
            return Response({"richMenuId": menu_id})
        if path.endswith("/content"):
            menu_id = path.split("/")[1]
            if method == "POST":
                self.images.add(menu_id)
                return Response({})
            return Response({}, status=200 if menu_id in self.images else 404)
        if path == "user/all/richmenu":
            return Response({"richMenuId": self.default} if self.default else {}, status=200 if self.default else 404)
        if method == "POST" and path.startswith("user/all/richmenu/"):
            self.default = path.rsplit("/", 1)[1]
        return Response({})

    def seed(self, module, names: dict):
        """把 ``names``（键 → 菜单名）作为已发布的菜单放上频道，返回对应的站点配置。"""
        config = {}
        for index, (k, name) in enumerate(sorted(names.items())):
            menu_id = f"richmenu-old-{index}"
            self.menus.append({"richMenuId": menu_id, "name": name})
            self.images.add(menu_id)
            config[k] = menu_id
        if module.__name__.endswith("rich_menu"):
            config["basic"], config["query"] = config["th:basic"], config["th:query"]
            self.default = config["basic"]
        return config


class Response:
    def __init__(self, body, status=200):
        self.status_code, self._body, self.text = status, body, ""
        self.content = b"{}" if body else b""

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

    def stage(self, product, channel, *, can_send=True, version="v2"):
        module = importlib.import_module(MODULES[product])
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
            patch.object(module, "payload", lambda *args: {"areas": [], "args": repr(args), "v": version}),
            patch.object(module, "_image", lambda *args: (repr(args) + version).encode()),
        ]
        stack.append(patch.object(module, "_state_store", lambda: store) if hasattr(module, "_state_store")
                     else patch.object(module.pearnly_state, "store", lambda: store))
        for p in stack:
            p.start()
            self.addCleanup(p.stop)
        return module

    def published_old(self, product, channel):
        """频道上挂着上一版（v1）的菜单，站点配置指着它们。"""
        old = self.stage(product, channel, version="v1")
        names = old.wanted()
        patch.stopall()
        self.conf[old.CONFIG] = channel.seed(old, names)

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
                self.assertEqual(json.loads(lines[0].removeprefix("BEFORE "))["config"], before)
                self.assertTrue(line_surfaces.clean(module.drift()))
                self.assertIn(product, self.reconciled)
                if product == "dms":
                    self.assertEqual(channel.default, self.conf[module.CONFIG]["th:basic"])
                patch.stopall()
                self.reconciled.clear()

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

    def run_main(self, sites):
        fake, lines = FakeFrappe(sites), []
        with patch.object(runner, "frappe", fake), patch.object(runner, "_sites", lambda only: iter(sorted(sites))), \
                patch.object(runner.line_surfaces, "check_site", lambda product: fake.conf["__rows"]), \
                patch("builtins.print", lambda text, **k: lines.append(text)):
            code = runner.main(["check", "--product", "cowork"])
        summary = [line for line in lines if line.startswith("SUMMARY ")]
        return code, json.loads(summary[0].removeprefix("SUMMARY "))

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
