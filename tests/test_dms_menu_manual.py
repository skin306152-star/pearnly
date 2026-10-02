"""Zero-I/O checks for DMS LINE menu item 6 「คู่มือ」 (tutorial videos, customer Korn 2026-10).

Loads the real menu, card, output and rich-menu modules over small stand-ins for Frappe,
the LINE transport and the session store. Nothing sends LINE messages or touches a Site.
"""
from __future__ import annotations

import csv
import importlib
from pathlib import Path
import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_dms/pearnly_dms"
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

VIDEO = "https://media.example.test/dms/manual/6.1.mp4"
COVER = "https://media.example.test/dms/manual/6.1.jpg"


def data(postback_data):
    return {key: values[0] for key, values in parse_qs(postback_data).items()}


class Isolated(unittest.TestCase):
    """Fresh copies of the Pearnly modules over stand-ins; LINE output is recorded."""

    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name in ("frappe", "requests") or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
                del sys.modules[name]
        self.conf = {}
        self.frappe = self.stub("frappe", _=lambda text: text, conf=self.conf, flags={},
                                local=types.SimpleNamespace(site="lab.localhost", lang="th"))
        self.frappe.utils = self.stub("frappe.utils", get_url=lambda: "https://tenant.example.test")
        self.stub("requests")
        self.stub("pearnly_core", __path__=[])
        self.replies, self.pushes = [], []
        self.stub("pearnly_core.line",
                  reply=lambda token, msgs: self.replies.append((token, msgs)) or True,
                  push=lambda to, msgs, **kw: self.pushes.append((to, msgs)) or True,
                  reply_or_push=lambda to, msgs, token="", **kw: self.replies.append((token, msgs)) or True,
                  liff_id=lambda: "")

        @contextmanager
        def line_language(line_user_id="", *, language=None):
            yield language or "th"

        self.stub("pearnly_dms.i18n", t=lambda key, lang=None, **kw: key, line_lang=lambda: "th",
                  LANGUAGES=("th", "en", "zh", "ja"), line_language=line_language)
        self.cards = importlib.import_module("pearnly_dms.line.cards")

    def stub(self, name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        parent, _, child = name.rpartition(".")
        if parent in sys.modules:
            setattr(sys.modules[parent], child, module)
        return module


class MenuCardTests(Isolated):
    def card_rows(self, **kwargs):
        menu_cards = importlib.import_module("pearnly_dms.menu.menu_cards")
        body = menu_cards.menu_card(**kwargs)["contents"]["body"]["contents"]
        return [row for row in body if row.get("type") == "box" and "action" in row]

    def test_text_menu_gains_row_six_for_everyone(self):
        for can_query in (False, True):
            rows = self.card_rows(can_query=can_query)
            last = rows[-1]
            self.assertEqual(data(last["action"]["data"]), {"action": "menu_manual"})
            texts = [part.get("text") for part in last["contents"] if part.get("type") == "text"]
            self.assertIn("6", texts)
            title = last["contents"][2]["contents"][0]["text"]
            self.assertEqual(title, "dms.card.TXT_MENU_ITEM6")
        self.assertEqual(len(self.card_rows(can_query=True)), 4)  # 1, 2, 5, 6 (3/4 need LIFF)

    def test_row_six_uses_its_shipped_icon(self):
        icon = self.card_rows()[-1]["contents"][0]["contents"][0]
        self.assertEqual(icon["type"], "image")
        self.assertTrue(icon["url"].endswith("/menu-6.png?v=1"))
        art = Path(__file__).resolve().parents[1] / "apps/pearnly_dms/pearnly_dms/public/images/menu"
        self.assertTrue((art / "menu-6.png").is_file())


class ManualTests(Isolated):
    def setUp(self):
        super().setUp()
        self.manual = importlib.import_module("pearnly_dms.menu.manual")

    def test_submenu_has_three_buttons(self):
        card = self.manual.manual_card()
        buttons = card["contents"]["footer"]["contents"]
        self.assertEqual([button["action"]["label"] for button in buttons],
                         ["6.0 dms.card.TXT_MANUAL_SETUP", "6.1 dms.card.TXT_MANUAL_CUSTOMER",
                          "6.2 dms.card.TXT_MANUAL_BOOKING"])
        self.assertEqual([data(button["action"]["data"]) for button in buttons],
                         [{"action": "manual_video", "item": code} for code in ("setup", "customer", "booking")])
        self.assertTrue(all(button["action"]["type"] == "postback" for button in buttons))

    def test_configured_video_is_a_reply_with_the_right_fields(self):
        self.conf["pearnly_dms_manual_customer"] = {"video": VIDEO, "preview": COVER}
        self.manual.play("tok", "customer")
        self.assertEqual(self.replies, [("tok", [{"type": "video", "originalContentUrl": VIDEO,
                                                  "previewImageUrl": COVER}])])
        self.assertEqual(self.pushes, [])

    def test_config_written_as_json_text_also_works(self):
        self.conf["pearnly_dms_manual_setup"] = '{"video": "%s", "preview": "%s"}' % (VIDEO, COVER)
        self.assertEqual(self.manual.video_message("setup")["originalContentUrl"], VIDEO)

    def test_missing_or_bad_config_says_not_online_yet(self):
        cases = {
            "booking": None,
            "setup": {"video": VIDEO},
            "customer": {"video": VIDEO.replace("https", "http"), "preview": COVER},
        }
        for code, value in cases.items():
            self.conf.clear()
            if value is not None:
                self.conf["pearnly_dms_manual_" + code] = value
            self.replies.clear()
            self.manual.play("tok", code)
            self.assertEqual(self.replies, [("tok", [{"type": "text", "text": "dms.card.TXT_MANUAL_NOT_READY"}])],
                             code)
        self.manual.play("tok", "unknown")
        self.assertEqual(self.replies[-1][1][0]["text"], "dms.card.TXT_MANUAL_NOT_READY")
        self.assertEqual(self.pushes, [])

    def test_no_reply_token_sends_nothing(self):
        self.conf["pearnly_dms_manual_setup"] = {"video": VIDEO, "preview": COVER}
        self.manual.play("", "setup")
        self.manual.open_manual("")
        self.assertEqual((self.replies, self.pushes), ([], []))


class FakeStore:
    def __init__(self, session=None):
        self.session = session
        self.writes = []

    def get_session(self, line_user_id):
        return self.session

    def set_session(self, line_user_id, state, payload):
        self.writes.append((state, payload))
        self.session = {"state": state, "payload": payload}

    def clear_session(self, line_user_id):
        self.writes.append(("cleared", None))


class MenuFlowTests(Isolated):
    def setUp(self):
        super().setUp()
        self.store = FakeStore()
        self.stub("pearnly_core.line_session", get_session=self.store.get_session,
                  set_session=self.store.set_session, clear_session=self.store.clear_session,
                  verify_nonce=lambda *a: False)
        self.stub("pearnly_dms.booking.conversation.booking_qa", start=Mock())
        self.stub("pearnly_dms.query.query_access", can_query=lambda binding: False)
        self.menu = importlib.import_module("pearnly_dms.menu.menu_flow")

    def test_postbacks_open_the_submenu_and_play_without_touching_the_session(self):
        sess = {"state": "booking_qa", "payload": {"qa": {"step": "place"}}}
        self.store.session = sess
        self.assertIn(self.cards.ACT_MENU_MANUAL, self.menu.MENU_ACTIONS)
        self.assertIn(self.cards.ACT_MANUAL_VIDEO, self.menu.MENU_ACTIONS)
        self.menu.handle_postback({"user_id": "u"}, "U1", "tok", self.cards.ACT_MENU_MANUAL, {}, sess)
        self.assertEqual(self.replies[-1][1][0]["altText"], "dms.card.TXT_MANUAL_PICK")
        self.conf["pearnly_dms_manual_booking"] = {"video": VIDEO, "preview": COVER}
        self.menu.handle_postback({"user_id": "u"}, "U1", "tok2", self.cards.ACT_MANUAL_VIDEO,
                                  {"item": "booking"}, sess)
        self.assertEqual(self.replies[-1], ("tok2", [{"type": "video", "originalContentUrl": VIDEO,
                                                      "previewImageUrl": COVER}]))
        self.assertEqual(self.store.writes, [], "watching the manual keeps the booking in progress")
        self.assertEqual(self.pushes, [])

    def test_typing_six_in_the_menu_opens_the_manual(self):
        self.store.session = {"state": "menu", "payload": {}}
        self.assertTrue(self.menu.handle_choice({"user_id": "u"}, "U1", "tok", self.store.session, "6"))
        self.assertEqual(self.replies[-1][1][0]["altText"], "dms.card.TXT_MANUAL_PICK")
        self.assertFalse(self.menu.handle_choice({"user_id": "u"}, "U1", "tok", self.store.session, "66"))


class PostbackRoutingTests(Isolated):
    def test_manual_postbacks_reach_the_menu_layer(self):
        self.stub("pearnly_core.line_session", get_session=lambda uid: None)
        for name in ("pearnly_dms.intake.draft", "pearnly_dms.intake.service", "pearnly_dms.ocr.ocr_review",
                     "pearnly_dms.ocr.ocr_service", "pearnly_dms.ocr.timing", "pearnly_dms.line.out",
                     "pearnly_dms.line.identity", "pearnly_dms.config.pacing"):
            self.stub(name)
        sys.modules["pearnly_dms.line.identity"].matches = lambda *a: True
        sys.modules["pearnly_dms.ocr.timing"].traced = lambda name: (lambda function: function)
        self.stub("pearnly_dms.booking.conversation.booking_flow", BOOKING_ACTIONS=frozenset())
        self.stub("pearnly_dms.query.query_flow", QUERY_ACTIONS=frozenset())
        self.stub("pearnly_dms.approval.approval_flow", APPROVAL_ACTIONS=frozenset())
        menu = self.stub("pearnly_dms.menu.menu_flow", handle_postback=Mock(),
                         MENU_ACTIONS=frozenset({self.cards.ACT_MENU_MANUAL, self.cards.ACT_MANUAL_VIDEO}))
        flow = importlib.import_module("pearnly_dms.intake.flow")
        flow.handle_postback({"user_id": "u"}, "U1", "tok", {"data": "action=manual_video&item=setup"})
        args = menu.handle_postback.call_args.args
        self.assertEqual((args[3], args[4]), ("manual_video", {"action": "manual_video", "item": "setup"}))


class RichMenuTests(Isolated):
    def setUp(self):
        super().setUp()
        self.stub("pearnly_core.line_binding")
        self.stub("pearnly_dms.query.query_access", can_query=lambda binding: False)
        self.stub("pearnly_dms.menu.links", account_action=lambda kind, label: {
            "type": "uri", "label": str(label), "uri": "https://liff.line.me/x?action=" + kind,
            "altUri": {"desktop": "https://x"}})
        self.rich = importlib.import_module("pearnly_dms.menu.rich_menu")

    def test_query_menu_fills_its_sixth_cell_with_the_manual(self):
        areas = self.rich.payload(query=True)["areas"]
        self.assertEqual(len(areas), 6)
        last = areas[-1]
        self.assertEqual(last["action"], {"type": "postback", "label": "dms.card.TXT_MENU_ITEM6"[:20],
                                          "data": "action=menu_manual"})
        self.assertEqual(last["bounds"], {"x": 1666, "y": 843, "width": 834, "height": 843})

    def test_basic_menu_ends_with_a_wide_manual_card(self):
        areas = self.rich.payload(query=False)["areas"]
        self.assertEqual([a["bounds"]["width"] for a in areas], [833, 833, 834, 1250, 1250])
        self.assertEqual(areas[-1]["action"]["data"], "action=menu_manual")
        self.assertEqual(areas[-1]["bounds"], {"x": 1250, "y": 843, "width": 1250, "height": 843})
        self.assertNotIn("menu_query", str(areas))


class GlossaryTests(unittest.TestCase):
    KEYS = ("TXT_MENU_ITEM6", "TXT_MENU_D6", "TXT_MANUAL_PICK", "TXT_MANUAL_PICK_SUB", "TXT_MANUAL_SETUP",
            "TXT_MANUAL_CUSTOMER", "TXT_MANUAL_BOOKING", "TXT_MANUAL_NOT_READY",
            "rich.manual.title", "rich.manual.sub")

    def test_every_new_text_has_four_languages(self):
        with open(APP / "i18n/messages.tsv", encoding="utf-8") as handle:
            rows = {row[4]: row[:4] for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
                    if len(row) >= 5}
        for key in self.KEYS:
            values = rows.get("dms.card." + key)
            self.assertIsNotNone(values, key)
            self.assertTrue(all(value.strip() for value in values), key)
        self.assertEqual(rows["dms.card.TXT_MENU_ITEM6"][2], "คู่มือ")
        for code in ("setup", "customer", "booking"):
            self.assertIn("dms.card.TXT_MANUAL_" + code.upper(), rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
