"""所有登录 MR.ERP 的地方走同一套会话复用与被踢标记（11-11 风险 7 真站点实测后，主会话定按类修）。

真站点实测：网页「提交」前读菜单 / 主档每次都新登录（12:23:37、12:23:39 各一次），每次都把正在浏览器里用同一账号的会计踢下线，
也踢掉缓存里的推送会话。现在推送、读菜单 / 主档、目录采集都从 `pearnly_core.mrerp.account.use` 拿会话：同账号锁 → 被踢标记 →
接上缓存会话（过期重登一次）→ 被踢记标记、丢会话。

「人点的」标在调用入口上（主会话 11-11）：读取函数必须显式传 ``manual``；网页与 CC 共用的申请接口（Cowork
`connections.api.request_refresh / request_mrerp_menu`，ERP `mrerp.api` 的三个申请）传 True，后台任务默认 False。
CC 的工具只在用户这一轮里执行（`assistant.toolrun`），对话外没有读取。哪些入口可以传 ``manual``，由
`test_erp_mrerp_direct.KickedAccount.test_only_the_manual_entries_clear_the_mark` 按函数登记锁住（与风险 7 的推送入口同一份）。
"""
import contextlib
import inspect
import pathlib
import re
import socket
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    import requests
    from pearnly_core.mrerp import collect, master_export, menu_read
    from pearnly_core.mrerp.push import transport
    from test_cowork_mrerp_push import BASE, PUBLIC, FakeMrErp, _StateRedis, execute, sales
except (ImportError, unittest.SkipTest) as exc:  # 需要 Frappe 环境（bench 的 Python）
    raise unittest.SkipTest(f"needs the Frappe bench environment: {exc}")

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEASE = {"system_url": BASE, "username": "fakeuser", "password": "fakepw"}


@contextlib.contextmanager
def site_calls(site):
    """假站点接管所有 HTTP；同账号锁换成空锁（锁本身另有测试）。"""
    def send(session, method, url, **kwargs):
        r = site(session, method, url, **kwargs)
        for hook in session.hooks["response"]:
            hook(r)
        return r

    import pearnly_core.mrerp as package

    holders = [module for module in (menu_read, collect, transport, getattr(package, "account", None))
               if module is not None and hasattr(module, "account_lock")]
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(requests.Session, "request", autospec=True, side_effect=send))
        stack.enter_context(patch.object(socket, "getaddrinfo", return_value=PUBLIC))
        for module in holders:
            stack.enter_context(patch.object(module, "account_lock", lambda *a, **k: contextlib.nullcontext(True)))
        yield


class SharedSession(_StateRedis):
    def pushed(self):
        site = FakeMrErp()
        site.password_used = "fakepw"
        self.assertEqual(execute(site, [sales("r1")])["status"], "success")
        self.assertEqual(len(self.logins(site)), 1)
        return site

    def test_reading_menus_after_a_push_reuses_its_session(self):
        site = self.pushed()
        with site_calls(site):
            pages, _fields, error, _master = menu_read.read(LEASE, "15:1", manual=False)
        self.assertIsNone(error)
        self.assertTrue(pages)
        self.assertEqual(len(self.logins(site)), 1)  # 没再登录，推送的会话还在
        self.assertEqual(list(self.cached().values()), ["abc"])

    def test_collecting_the_catalog_after_a_push_reuses_its_session(self):
        site = self.pushed()
        with site_calls(site):
            out = collect.collect(LEASE, manual=False)
        self.assertNotIn("error_code", out)
        self.assertEqual(len(self.logins(site)), 1)

    def test_a_read_that_finds_the_account_kicked_marks_it_and_drops_the_session(self):
        site = self.pushed()
        site.kick = True  # 会计本人在浏览器里登录了同一账号
        with site_calls(site):
            _pages, _fields, error, _master = menu_read.read(LEASE, "15:1", manual=False)
        self.assertEqual(error, "ERR_ACCOUNT_IN_USE")
        self.assertEqual(len(self.logins(site)), 1)  # 不重登去抢
        self.assertTrue(self.kick.state(self.account()))
        self.assertEqual(self.cached(), {})

    def test_a_background_read_of_a_marked_account_sends_nothing(self):
        self.kick.mark(self.account())
        site = FakeMrErp()
        with site_calls(site):
            self.assertEqual(menu_read.read(LEASE, "15:1", manual=False)[2], "ERR_ACCOUNT_IN_USE")
            self.assertEqual(collect.collect(LEASE, manual=False).get("error_code"), "ERR_ACCOUNT_IN_USE")
        self.assertEqual(site.calls, [])

    def test_a_manual_read_clears_the_mark_and_logs_in_again(self):
        self.kick.mark(self.account())
        site = FakeMrErp()
        with site_calls(site):
            _pages, _fields, error, _master = menu_read.read(LEASE, "15:1", manual=True)
        self.assertIsNone(error)
        self.assertIsNone(self.kick.state(self.account()))
        self.assertEqual(len(self.logins(site)), 1)
        self.assertEqual(list(self.cached().values()), ["abc"])  # 这次登录的会话存下给推送接着用


MASTER_LISTS = {"exparmas": {"txtarcodebegin": [[1, "C01", "ร้านป้าแดง"]]},
                "expapmas": {"txtapcodebegin": [[1, "S01", "บางจาก"]]},
                "expstkmas": {"txtstkcodebegin": [[1, "P01", "น้ำแข็ง"]]}}


class WarmProbe(_StateRedis):
    """用时再核（11-22）：MR.ERP 没有修改时间可查，只在会话还热时探测三张列表的摘要，**绝不为探测登录**（登录会把会计在浏览器
    里同一账号踢下线；主会话 11-22 定：不能因为探测新增任何登录次数）。"""

    def pushed(self, lists=None):
        site = FakeMrErp(lists={**MASTER_LISTS, **(lists or {})})
        site.password_used = "fakepw"
        self.assertEqual(execute(site, [sales("p1")])["status"], "success")
        self.assertEqual(len(self.logins(site)), 1)
        return site

    def test_a_probe_reuses_the_warm_session_and_never_logs_in(self):
        site = self.pushed()
        with site_calls(site):
            seen, error = master_export.probe(LEASE, "15:1")
        self.assertIsNone(error)
        self.assertTrue(seen)
        self.assertEqual(len(self.logins(site)), 1)  # 只有推送那一次

    def test_without_a_warm_session_a_probe_sends_nothing(self):
        site = FakeMrErp(lists=MASTER_LISTS)
        with site_calls(site):
            self.assertEqual(master_export.probe(LEASE, "15:1"), (None, "ERR_NO_SESSION"))
        self.assertEqual(site.calls, [])

    def test_an_expired_warm_session_is_dropped_without_logging_in(self):
        site = self.pushed()
        site.accepted = False  # 服务器上的会话过期了：进账套被跳回登录页
        with site_calls(site):
            self.assertEqual(master_export.probe(LEASE, "15:1"), (None, "ERR_NO_SESSION"))
        self.assertEqual(len(self.logins(site)), 1)
        self.assertEqual(self.cached(), {})  # 丢掉，下次照常判

    def test_a_marked_account_is_not_probed(self):
        site = self.pushed()
        self.kick.mark(self.account())
        before = len(site.calls)
        with site_calls(site):
            self.assertEqual(master_export.probe(LEASE, "15:1")[1], "ERR_ACCOUNT_IN_USE")
        self.assertEqual(len(site.calls), before)

    def test_the_digest_moves_with_a_new_or_renamed_record_only(self):
        site = self.pushed()
        with site_calls(site):
            first, _ = master_export.probe(LEASE, "15:1")
            again, _ = master_export.probe(LEASE, "15:1")
            site.lists["exparmas"] = {"txtarcodebegin": [[1, "C01", "ร้านป้าแดง"], [2, "C02", "ร้านใหม่"]]}
            added, _ = master_export.probe(LEASE, "15:1")
        self.assertEqual(first, again)
        self.assertNotEqual(first, added)
        self.assertEqual(len(self.logins(site)), 1)


class ManualOnlyAtTheEntries(unittest.TestCase):
    """「人点的」只标在调用入口上；共用层的读取函数不给默认值，后台调用方不传就清不了标记。"""

    def test_the_shared_reads_take_manual_explicitly(self):
        for function in (menu_read.read, collect.collect):
            with self.subTest(function=function.__name__):
                parameter = inspect.signature(function).parameters.get("manual")
                self.assertIsNotNone(parameter)
                self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_mrerp_sessions_are_only_opened_in_the_shared_entry(self):
        opened = set()
        for path in (ROOT / "apps").rglob("*.py"):
            if re.search(r"\bMrErpSession\(", path.read_text(encoding="utf-8")):
                opened.add(str(path.relative_to(ROOT)))
        self.assertEqual(opened, {"apps/pearnly_core/pearnly_core/mrerp/account.py"})


if __name__ == "__main__":
    unittest.main()
