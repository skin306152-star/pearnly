"""DMS 连接页「真试一次再存」放后台跑（2026-10-02：同步请求会撞 gunicorn 120 秒）。在生产镜像里跑，不连站点、不登 DMS。"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_dms"):
    sys.path.insert(0, str(ROOT / "apps" / app))
for name in [n for n in sys.modules if n.split(".")[0] in ("pearnly_core", "pearnly_dms")]:
    del sys.modules[name]

import frappe  # noqa: E402

from pearnly_dms.connections import background  # noqa: E402

assert Path(background.__file__).is_relative_to(ROOT), background.__file__
TOKEN = "check-abcdefgh"


class Store:
    def __init__(self):
        self.data = {}

    def set_value(self, key, value, expires_in_sec=None):
        self.data[key] = value

    def get_value(self, key):
        return self.data.get(key)

    def delete_value(self, key):
        self.data.pop(key, None)


def _throw(message, exc=None, *args, **kwargs):
    raise (exc or frappe.ValidationError)(message)


class AccountCheck(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        frappe.local.site, frappe.local.lang = "dms.example.invalid", "th"
        frappe.local.session = frappe._dict(user="admin@example.invalid")
        identity = lambda text, *a, **kw: text  # 没有站点，不加载翻译
        for target, kwargs in (("pearnly_core.execution.state.store", {"return_value": self.store}),
                               ("frappe.db", {"new": MagicMock()}), ("frappe.throw", {"new": _throw}),
                               ("frappe._", {"new": identity}), ("pearnly_dms.connections.background._", {"new": identity}),
                               ("frappe.publish_realtime", {"new": MagicMock()}), ("frappe.set_user", {"new": MagicMock()}),
                               ("frappe.utils.password.encrypt", {"new": lambda text: "sealed:" + text}),
                               ("frappe.utils.password.decrypt", {"new": lambda text: text.removeprefix("sealed:")})):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def start(self, kind="read", secrets=None):
        with patch("frappe.enqueue") as enqueue:
            reply = background.start(kind, TOKEN, secrets or {"username": "reader", "password": "s3cret", "system_url": None})
        return reply, enqueue.call_args

    def test_start_queues_without_the_password(self):
        reply, call = self.start()
        self.assertEqual(reply, {"queued": TOKEN})
        self.assertEqual((call.args[0], call.kwargs["queue"], call.kwargs["kind"]), ("pearnly_dms.connections.background.run", "long", "read"))
        self.assertNotIn("s3cret", json.dumps(call.kwargs, default=str))
        sealed = [value for key, value in self.store.data.items() if key.endswith(":secret")]
        self.assertEqual(len(sealed), 1)
        self.assertTrue(sealed[0].startswith("sealed:"))
        with patch.object(background, "_job_alive", return_value=True):
            self.assertEqual(background.status(TOKEN), {"state": "queued"})

    def run_job(self, behaviour):
        with patch("frappe.get_attr", return_value=behaviour) as get_attr, patch("frappe.log_error") as log_error:
            background.run("read", TOKEN, "admin@example.invalid", "th")
        with patch.object(background, "_job_alive", return_value=False):
            return background.status(TOKEN), get_attr, log_error

    def test_the_check_runs_with_the_secret_once_and_reports_the_reply(self):
        self.start()
        seen = []
        state, get_attr, _log = self.run_job(lambda **kw: seen.append(kw) or {"ok": True, "reading": "started"})
        self.assertEqual(seen, [{"username": "reader", "password": "s3cret", "system_url": None}])
        self.assertEqual(get_attr.call_args.args[0], "pearnly_dms.connections.api._set_read_account")
        self.assertEqual(state, {"state": "done", "result": {"ok": True, "reading": "started"}})
        self.assertFalse([key for key in self.store.data if key.endswith(":secret")], "the sealed secret is deleted once read")
        payload = frappe.publish_realtime.call_args
        self.assertEqual((payload.args[0], payload.args[1], payload.kwargs["user"]),
                         ("pearnly_dms_account_check", {"token": TOKEN, "state": "done"}, "admin@example.invalid"))

    def test_reasons_for_people_reach_the_page_and_errors_are_logged(self):
        self.start()

        def taken(**_):
            raise frappe.ValidationError("This account is already used by an operator or another system account")
        state, _get, log_error = self.run_job(taken)
        self.assertEqual(state["state"], "failed")
        self.assertIn("already used", state["message"])
        log_error.assert_not_called()
        self.start()

        def boom(**_):
            raise RuntimeError("playwright crashed")
        state, _get, log_error = self.run_job(boom)
        self.assertEqual(state["state"], "failed")
        self.assertNotIn("playwright", state["message"])
        log_error.assert_called_once()

    def test_a_dead_job_reads_as_failed_and_status_never_returns_secrets(self):
        self.start()
        with patch.object(background, "_job_alive", return_value=False):
            state = background.status(TOKEN)
        self.assertEqual(state["state"], "failed")
        self.assertNotIn("job", state)
        self.assertNotIn("s3cret", json.dumps(state))
        self.assertEqual(background.status("../bad"), {})


if __name__ == "__main__":
    unittest.main()
