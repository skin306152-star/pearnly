#!/usr/bin/env python3
"""No network: provisioning creates or corrects each tenant's LIFF on the shared LINE Login channel (2026-10-05)."""
import importlib.util
import json
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "apps/pearnly_control/pearnly_control/onboarding/line_liff.py"
spec = importlib.util.spec_from_file_location("line_liff", path)
liff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(liff)

CHANNEL = "2010411313"
SITE = "acme-erp.pearnly.com"


class Response:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    @property
    def content(self):
        return b"" if self._body is None else json.dumps(self._body).encode()

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return self._body


class FakeLine:
    """Stands in for the LINE Login stateless token endpoint and the LIFF server API."""

    def __init__(self, apps=(), token_status=200):
        self.apps = [dict(a) for a in apps]
        self.token_status, self.calls, self.next = token_status, [], 1

    def post(self, url, data, timeout):
        self.calls.append(("TOKEN", data["client_id"], data["client_secret"]))
        return Response(self.token_status, {"access_token": "short"} if self.token_status == 200 else {"error": "invalid_client"})

    def request(self, method, url, json=None, timeout=None, headers=None):
        assert headers == {"Authorization": "Bearer short"}
        liff_id = url.rsplit("/", 1)[-1] if url.count("/apps/") else ""
        self.calls.append((method, liff_id, json))
        if method == "GET":
            return Response(200, {"apps": self.apps})
        if method == "POST":
            new = f"{CHANNEL}-new{self.next}"
            self.next += 1
            self.apps.append({"liffId": new, **json})
            return Response(200, {"liffId": new})
        app = next((a for a in self.apps if a["liffId"] == liff_id), None)
        if app is None:
            return Response(404, {"message": "not found"})
        if method == "PUT":
            app.update(json)
            return Response(200)
        self.apps.remove(app)
        return Response(200)

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PUT", "DELETE")]


def good(liff_id, site=SITE, product="erp"):
    return {"liffId": liff_id, **liff.desired(site, product)}


class Desired(unittest.TestCase):
    def test_settings_match_what_the_guide_used_to_ask_for(self):
        erp, dms = liff.desired(SITE, "erp"), liff.desired("acme-dms.pearnly.com", "dms")
        self.assertEqual(erp["view"], {"type": "full", "url": "https://acme-erp.pearnly.com/"})
        self.assertEqual(dms["view"]["url"], "https://acme-dms.pearnly.com/liff/dms-booking")
        self.assertEqual(erp["scope"], ["openid"])
        self.assertEqual(dms["scope"], ["openid", "chat_message.write"])
        self.assertEqual((erp["botPrompt"], erp["features"]), ("none", {"qrCode": False}))
        self.assertEqual(erp["description"], SITE)

    def test_names_never_contain_line(self):
        self.assertEqual(liff.desired("online-shop.pearnly.com", "cowork")["description"], "Pearnly COWORK")


class Ensure(unittest.TestCase):
    def ensure(self, line, product="erp", liff_id="", site=SITE):
        return liff.ensure(CHANNEL, "login-secret", site, product, liff_id, http=line)

    def test_new_tenant_gets_a_new_liff_with_the_standard_settings(self):
        line = FakeLine()
        self.assertEqual(self.ensure(line), f"{CHANNEL}-new1")
        self.assertEqual(line.calls[0], ("TOKEN", CHANNEL, "login-secret"))
        self.assertEqual(line.writes(), [("POST", "", liff.desired(SITE, "erp"))])

    def test_rerun_finds_the_same_liff_and_writes_nothing(self):
        line = FakeLine()
        first = self.ensure(line)
        self.assertEqual(self.ensure(line), first)
        self.assertEqual(self.ensure(line, liff_id=first), first)
        self.assertEqual(len(line.writes()), 1)

    def test_hand_made_liff_with_a_wrong_address_is_corrected_in_place(self):
        # 10-04: testcowork 少了 `.com`，testerp 填成了 DMS 的地址 —— 渠道上记着它的 ID，就地改对。
        wrong = {"liffId": f"{CHANNEL}-old", "view": {"type": "tall", "url": "https://acme-erp.pearnly/liff/dms-booking"},
                 "scope": ["openid"], "botPrompt": "normal"}
        line = FakeLine([wrong])
        self.assertEqual(self.ensure(line, liff_id=f"{CHANNEL}-old"), f"{CHANNEL}-old")
        self.assertEqual(line.writes(), [("PUT", f"{CHANNEL}-old", liff.desired(SITE, "erp"))])

    def test_working_hand_made_liffs_are_left_as_they_are(self):
        # 10-05 生产现状：mrerp 落在 /stocktake 多勾了 profile；erpcowork 落在 /desk/pearnly-intake；DMS 两站正确。
        cases = (("erp", {"type": "full", "url": "https://acme-erp.pearnly.com/stocktake"}, ["openid", "profile"]),
                 ("cowork", {"type": "full", "url": "https://acme-erp.pearnly.com/desk/pearnly-intake"}, ["openid"]),
                 ("dms", {"type": "full", "url": "https://acme-erp.pearnly.com/liff/dms-booking"},
                  ["chat_message.write", "openid"]))
        for product, view, scope in cases:
            with self.subTest(product=product):
                line = FakeLine([{"liffId": f"{CHANNEL}-old", "view": view, "scope": scope, "botPrompt": "none"}])
                self.assertEqual(self.ensure(line, product=product, liff_id=f"{CHANNEL}-old"), f"{CHANNEL}-old")
                self.assertEqual(line.writes(), [])

    def test_dms_liff_missing_the_free_reply_scope_is_corrected(self):
        line = FakeLine([{"liffId": f"{CHANNEL}-old", "view": {"type": "full", "url": "https://acme-erp.pearnly.com/liff/dms-booking"},
                          "scope": ["openid"], "botPrompt": "none"}])
        self.ensure(line, product="dms", liff_id=f"{CHANNEL}-old")
        self.assertEqual(line.writes(), [("PUT", f"{CHANNEL}-old", liff.desired(SITE, "dms"))])

    def test_liff_is_found_by_its_address_when_the_channel_has_no_id_yet(self):
        line = FakeLine([good(f"{CHANNEL}-other", site="other.pearnly.com"),
                         {**good(f"{CHANNEL}-mine"), "view": {"type": "full", "url": "https://acme-erp.pearnly.com"}}])
        self.assertEqual(self.ensure(line), f"{CHANNEL}-mine")
        self.assertEqual(line.writes(), [])

    def test_a_full_channel_is_reported_and_nothing_is_deleted(self):
        line = FakeLine([good(f"{CHANNEL}-{n}", site=f"t{n}.pearnly.com") for n in range(30)])
        with self.assertRaises(liff.LiffFull):
            self.ensure(line)
        self.assertEqual(line.writes(), [])

    def test_wrong_login_secret_stops_before_any_liff_call(self):
        line = FakeLine(token_status=400)
        with self.assertRaisesRegex(liff.LiffError, "refused"):
            self.ensure(line)
        self.assertEqual([c[0] for c in line.calls], ["TOKEN"])


class Remove(unittest.TestCase):
    def test_teardown_deletes_only_this_channels_liff(self):
        line = FakeLine([good(f"{CHANNEL}-mine")])
        self.assertTrue(liff.remove(CHANNEL, "s", f"{CHANNEL}-mine", http=line))
        self.assertFalse(liff.remove(CHANNEL, "s", f"{CHANNEL}-mine", http=line), "already gone is not an error")
        self.assertFalse(liff.remove(CHANNEL, "s", "1999999999-foreign", http=line))
        self.assertFalse(liff.remove(CHANNEL, "s", "", http=line))
        self.assertEqual([c[0] for c in line.writes()], ["DELETE", "DELETE"])


if __name__ == "__main__":
    unittest.main()
