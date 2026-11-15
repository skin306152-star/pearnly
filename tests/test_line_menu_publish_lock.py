"""LINE 菜单首次发布：锁要盖住整次发布，整次发布共用一个连接（ERP / Cowork / DMS）。

2026-10-27 polymer 开户：新频道建 ERP 28 套菜单约 145 秒，超过原来 120 秒的锁，
释放时 LockNotOwnedError，「配置 LINE 频道」整步记失败。

Run with Frappe installed (candidate image). No Site, database, LINE, Redis or network:
LINE, the lock store and the site config are stand-ins.
"""
import importlib
import unittest
from unittest.mock import patch

import frappe

MODULES = ("pearnly_erp_portal.integrations.line_menu", "pearnly_cowork.line.menu", "pearnly_dms.menu.rich_menu")


class FreshChannel:
    """一个刚开 Messaging API 的频道：没有任何菜单，也没有任何图。"""

    def __init__(self, menus=(), images=()):
        self.sessions, self.calls, self.count = set(), [], 0
        self.menus, self.images, self.checks, self.closed, self.names = list(menus), set(images), [], 0, []

    def request(self, method, url, *, session=None, **kwargs):
        self.sessions.add(id(session) if session is not None else None)
        self.calls.append((method, url))
        if url.endswith("/richmenu/list"):
            return Response({"richmenus": self.menus})
        if method == "POST" and url.endswith("/v2/bot/richmenu"):
            self.names.append((kwargs.get("json") or {}).get("name"))
            self.count += 1
            return Response({"richMenuId": f"richmenu-{self.count}"})
        if method == "GET" and url.endswith("/content"):
            # 问「有没有图」只许流式取（只看状态、不读正文），用完要关
            self.checks.append(kwargs.get("stream"))
            menu_id = url.rsplit("/", 2)[-2]
            return Response({}, status=200 if menu_id in self.images else 404, channel=self)
        if method == "POST" and url.endswith("/content"):
            self.images.add(url.rsplit("/", 2)[-2])
        return Response({})


class Response:
    def __init__(self, body, status=200, channel=None):
        self.status_code, self._body, self.text, self._channel = status, body, "", channel
        self.content = b"{}" if body else b""

    def close(self):
        if self._channel:
            self._channel.closed += 1

    def json(self):
        return self._body


class Store:
    def __init__(self):
        self.locks = []

    def make_key(self, key):
        return key

    def lock(self, key, *, timeout, blocking_timeout):
        self.locks.append(timeout)
        return Lock()


class Lock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PublishLock(unittest.TestCase):
    def publish(self, name, channel=None):
        module = importlib.import_module(name)
        channel, store = channel or FreshChannel(), Store()
        stack = [
            patch.object(module.line, "can_send", lambda: True),
            patch.object(module.line, "request", channel.request),
            patch.object(module, "reconcile", lambda: None),
            patch("frappe.installer.update_site_config", lambda *a, **k: None),
            patch.object(frappe, "conf", {}, create=True),
        ]
        stack.append(patch.object(module, "_state_store", lambda: store) if hasattr(module, "_state_store")
                     else patch.object(module.pearnly_state, "store", lambda: store))
        # 菜单的内容和图不是这里要测的：换成按参数区分的小替身，各套的名字照样不同
        stack.append(patch.object(module, "payload", lambda *args: {"areas": [], "args": repr(args)}))
        stack.append(patch.object(module, "_image", lambda *args: repr(args).encode()))
        for p in stack:
            p.start(); self.addCleanup(p.stop)
        module.publish()
        return module, channel, store

    def test_lock_covers_the_whole_first_publish(self):
        for name in MODULES:
            with self.subTest(name):
                module, channel, store = self.publish(name)
                self.assertEqual(store.locks, [module.PUBLISH_LOCK_SECONDS])
                # 每次 LINE 请求最多等 20 秒；锁至少要盖住 ERP 首次发布那段实际耗时的好几倍
                self.assertGreaterEqual(module.PUBLISH_LOCK_SECONDS, 600)
                self.assertGreater(channel.count, 0)

    def test_one_connection_for_every_request_of_a_publish(self):
        for name in MODULES:
            with self.subTest(name):
                _module, channel, _store = self.publish(name)
                self.assertNotIn(None, channel.sessions)
                self.assertEqual(len(channel.sessions), 1)
                uploads = [url for method, url in channel.calls if method == "POST" and url.endswith("/content")]
                self.assertEqual(len(uploads), channel.count)


    def test_image_check_reads_only_the_status(self):
        """11-13 testerp：问「有没有图」把整张图读下来，生产到 api-data.line.me 慢时超时、整次发布失败。"""
        for name in MODULES:
            with self.subTest(name):
                _module, channel, _store = self.publish(name)
                self.assertTrue(channel.checks)
                self.assertEqual(set(channel.checks), {True})
                self.assertEqual(channel.closed, len(channel.checks))

    def test_rerun_after_a_partial_publish_resumes(self):
        """中途失败后重跑：已建的菜单按名字复用、已有图的不再传，只补没做完的。"""
        for name in MODULES:
            with self.subTest(name):
                _module, first, _store = self.publish(name)
                names = list(first.names)
                self.assertEqual(len(names), first.count)
                made = [{"name": n, "richMenuId": f"richmenu-{i + 1}"} for i, n in enumerate(names)]
                half = {item["richMenuId"] for item in made[: len(made) // 2]}
                _module, second, _store = self.publish(name, FreshChannel(menus=made, images=half))
                self.assertEqual(second.count, 0)
                uploads = {url.rsplit("/", 2)[-2] for method, url in second.calls
                           if method == "POST" and url.endswith("/content")}
                self.assertEqual(uploads, {item["richMenuId"] for item in made} - half)


if __name__ == "__main__":
    unittest.main()
