#!/usr/bin/env python3
"""No network, no Frappe: which identity pool `set_oidc` may point a site at, and what it records.

Each product group signs in to its own pool. The only exception is a Cowork site opened before the pool split
(2026-09-26) on the business pool: it may stay there, move to the Cowork pool (pearnly_control.account.repool)
and, once moved, move back (rollback). The decision uses the site's own configuration, never the payload.
"""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[2] / "infrastructure/production/site-step.py"
spec = importlib.util.spec_from_file_location("site_step", path)
step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step)

SITE = "acme.pearnly.com"
BUSINESS = "https://auth.pearnly.com/realms/pearnly-business"
COWORK = "https://auth.pearnly.com/realms/pearnly-cowork"


def payload(issuer, **extra):
    return {"issuer": issuer, "client_id": SITE, "client_secret": "s", "native_account": True,
            "redirect_uri": f"https://{SITE}/api/method/pearnly_core.identity.callback", **extra}


class OidcPool(unittest.TestCase):
    def test_new_sites_sign_in_to_their_own_pool(self):
        values, changed = step.oidc_config(payload(COWORK), "cowork", SITE, {})
        self.assertEqual(values["pearnly_oidc_issuer"], COWORK)
        self.assertFalse(changed)
        self.assertNotIn("pearnly_oidc_origin_issuer", values)
        self.assertEqual(step.oidc_config(payload(BUSINESS), "erp", SITE, {})[0]["pearnly_product"], "erp")
        for group, wrong in (("cowork", BUSINESS), ("erp", COWORK), ("dms", COWORK)):
            with self.assertRaises(AssertionError):
                step.oidc_config(payload(wrong), group, SITE, {})

    def test_an_old_cowork_site_may_stay_on_the_business_pool(self):
        values, changed = step.oidc_config(payload(BUSINESS + "/"), "cowork", SITE, {"pearnly_oidc_issuer": BUSINESS})
        self.assertEqual((values["pearnly_oidc_issuer"], changed), (BUSINESS, False))

    def test_moving_to_the_cowork_pool_records_where_it_came_from(self):
        values, changed = step.oidc_config(payload(COWORK), "cowork", SITE, {"pearnly_oidc_issuer": BUSINESS})
        self.assertTrue(changed)
        self.assertEqual((values["pearnly_oidc_issuer"], values["pearnly_oidc_origin_issuer"]), (COWORK, BUSINESS))

    def test_a_moved_site_may_go_back_and_the_origin_is_kept(self):
        moved = {"pearnly_oidc_issuer": COWORK, "pearnly_oidc_origin_issuer": BUSINESS}
        values, changed = step.oidc_config(payload(BUSINESS), "cowork", SITE, moved)
        self.assertTrue(changed)
        self.assertEqual(values["pearnly_oidc_issuer"], BUSINESS)
        self.assertNotIn("pearnly_oidc_origin_issuer", values)  # 第一次换池时记下的，不改

    def test_a_cowork_site_that_never_was_on_the_business_pool_cannot_go_there(self):
        with self.assertRaises(AssertionError):
            step.oidc_config(payload(BUSINESS), "cowork", SITE, {"pearnly_oidc_issuer": COWORK})
        with self.assertRaises(AssertionError):
            step.oidc_config(payload(BUSINESS), "cowork", SITE, {})

    def test_only_the_two_platform_pools_ever(self):
        for other in ("https://auth.pearnly.com/realms/master", "https://evil.example/realms/pearnly-cowork"):
            with self.assertRaises(AssertionError):
                step.oidc_config(payload(other), "cowork", SITE, {"pearnly_oidc_issuer": BUSINESS})

    def test_client_redirect_and_realm_secrets_are_still_checked(self):
        current = {"pearnly_oidc_issuer": BUSINESS}
        for bad in ({"client_id": "other.pearnly.com"}, {"native_account": False},
                    {"redirect_uri": "https://other.pearnly.com/api/method/pearnly_core.identity.callback"},
                    {"admin_client_secret": "x"}, {"password_check_client_secret": "x"}):
            with self.assertRaises(AssertionError):
                step.oidc_config({**payload(COWORK), **bad}, "cowork", SITE, current)


if __name__ == "__main__":
    unittest.main()
