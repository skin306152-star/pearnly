"""Guarded local category tests. Synthetic data only, no paid model/ERP/LINE IO.

Per-test writes rollback; labelled setup and HTTP fixtures stay in the lab.
--install-fresh creates only the exact new classification Site, never overwrites.
--snapshot hashes historical evidence, settings and any native categories/memory.
"""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

BASE = runpy.run_path(str(Path(__file__).with_name("cowork-duplicates-lab-verify.py")))
connect, user, grant, PAGES = (BASE[key] for key in ("connect", "user", "grant", "PAGES"))
FRESH_SITE = "cowork-backend-classification.localhost"


def snapshot():
    result = {"history": BASE["snapshot"]()}
    for table in ("Cowork Invoice Category", "Cowork Supplier Category Memory"):
        rows = frappe.db.get_all(table, fields=["*"], order_by="name", limit_page_length=0) if frappe.db.table_exists(table) else []
        result[table] = {"count": len(rows), "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()}
    return result


class ClassificationChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_cowork.masters import api
        suffix = uuid4().hex[:10]
        cls.actor, cls.reader, cls.outsider = [f"cw-category-{role}-{suffix}@example.invalid" for role in ("actor", "reader", "outsider")]
        user(cls.actor, ["Cowork Intake"])
        user(cls.reader, ["Cowork OCR History"])
        user(cls.outsider, ["Cowork Intake"])
        cls.workspace = api.create_workspace("Category lab " + suffix)["name"]
        cls.other = api.create_workspace("Category other " + suffix)["name"]
        cls.client = api.create_client(cls.workspace, "Buyer A")["name"]
        cls.client_b = api.create_client(cls.workspace, "Buyer B")["name"]
        for actor in (cls.actor, cls.reader):
            grant(actor, "Cowork Workspace", cls.workspace)
        grant(cls.actor, "Cowork Workspace", cls.other)
        cls.permission = grant(cls.actor, "Cowork Client", cls.client).name
        grant(cls.actor, "Cowork Client", cls.client_b)
        frappe.db.commit()

    def setUp(self):
        from pearnly_cowork.history import categories, storage
        self.c, self.s = categories, storage
        self.conf = dict(frappe.conf)
        frappe.set_user(self.actor)

    def tearDown(self):
        frappe.db.rollback()
        frappe.conf.clear()
        frappe.conf.update(self.conf)
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def create(self, *, client=None, key=None, fields=None, workspace=None):
        pages = copy.deepcopy(PAGES)
        pages[0]["fields"].update({"seller_name": "PTT", "category": "model raw"})
        pages[0]["fields"].update(fields or {})
        return self.s.create_recognized(workspace or self.workspace, pages, key or uuid4().hex,
            client=client, source="manual", confidence="high", ai_raw={"raw": "model raw"})

    def learn(self, client=None):
        row = self.create(client=client)
        pages = copy.deepcopy(row["pages"])
        pages[0]["fields"]["category"] = "ค่าไฟฟ้า"
        return self.s.update_pages(row["name"], pages, row["record_version"])

    def test_get_has_no_seed_write_and_recognition_seeds_once(self):
        self.assertEqual(self.c.tree(self.workspace), [])
        self.assertEqual(frappe.db.count(self.c.CATEGORY, {"workspace": self.workspace}), 0)
        row = self.create()
        self.assertEqual(row["pages"][0]["fields"]["category"], "ค่าน้ำมันเชื้อเพลิง")
        count = frappe.db.count(self.c.CATEGORY, {"workspace": self.workspace})
        self.assertGreater(count, 80)
        self.create()
        self.assertEqual(frappe.db.count(self.c.CATEGORY, {"workspace": self.workspace}), count)
        self.assertEqual(row["ai_raw"], {"raw": "model raw"})
        self.assertNotIn("category_tag", row)

    def test_review_memory_overrides_rule_and_feeds_express_account(self):
        self.learn()
        row = self.create(fields={"seller_name": "ptt"})
        category = row["pages"][0]["fields"]["category"]
        self.assertEqual(category, "ค่าไฟฟ้า")
        from pearnly_core.express_rules.push.common import resolve_account_sourced
        self.assertEqual(resolve_account_sourced([{"erp_type": "express", "pearnly_category": category,
            "erp_code": "5500"}], category, "9999"), ("5500", "category_map"))

    def test_memory_company_and_client_isolation(self):
        self.learn(self.client)
        self.assertEqual(self.create(client=self.client)["pages"][0]["fields"]["category"], "ค่าไฟฟ้า")
        for args in ({"client": self.client_b}, {}, {"workspace": self.other}):
            self.assertEqual(self.create(**args)["pages"][0]["fields"]["category"], "ค่าน้ำมันเชื้อเพลิง")

    def test_replay_does_not_reclassify_after_learning(self):
        first = self.create(key="stable")
        self.learn()
        with patch.object(self.c, "prepare", side_effect=AssertionError("must not run")):
            replay = self.create(key="stable")
        self.assertEqual(replay["pages"], first["pages"])
        with self.assertRaises(frappe.ValidationError):
            self.create(key="stable", fields={"invoice_number": "changed"})

    def test_manual_create_and_unchanged_review_do_not_learn(self):
        row = self.s.create_manual(self.workspace, PAGES, uuid4().hex)
        self.assertEqual(frappe.db.count(self.c.CATEGORY, {"workspace": self.workspace}), 0)
        self.s.update_pages(row["name"], row["pages"], row["record_version"])
        self.assertEqual(frappe.db.count(self.c.MEMORY, {"workspace": self.workspace}), 0)

    def test_first_primary_only_and_no_original_mutation(self):
        pages = copy.deepcopy(PAGES)
        pages[0]["fields"].update(seller_name="PTT", category="raw")
        original = copy.deepcopy(pages)
        result = self.c.prepare(self.workspace, None, pages)
        self.assertEqual(pages, original)
        self.assertNotEqual(result, pages)
        self.c.learn(self.workspace, None, [{"is_copy": True, "fields": {"seller_name": "OTHER", "category": "goods"}},
            {"fields": {"seller_name": "PTT", "category": "fuel"}}, {"fields": {"seller_name": "LAST", "category": "food"}}])
        self.assertEqual(frappe.db.count(self.c.MEMORY, {"workspace": self.workspace}), 1)
        self.assertEqual(self.c._memory(self.workspace, None, "PTT"), "fuel")

    def test_tree_management_two_levels_scope_revision_and_replay(self):
        frappe.set_user("Administrator")
        parent = self.c.save(self.workspace, "Custom", request_key="parent")
        child = self.c.save(self.workspace, "Custom child", parent=parent["name"], request_key="child")
        self.assertEqual(parent, self.c.save(self.workspace, "Custom", request_key="parent"))
        for scope_name, parent_name in ((self.workspace, child["name"]), (self.other, parent["name"])):
            with self.assertRaises(frappe.ValidationError):
                self.c.save(scope_name, "Forbidden", parent=parent_name, request_key=uuid4().hex)
        with self.assertRaises(frappe.ValidationError):
            self.c.save(self.workspace, "changed", request_key="parent")
        updated = self.c.save(self.workspace, "Renamed", name=parent["name"], expected_revision=1)
        self.assertEqual(updated["revision"], 2)
        with self.assertRaises(frappe.ValidationError):
            self.c.save(self.workspace, "stale", name=parent["name"], expected_revision=1)
        with self.assertRaises(frappe.PermissionError):
            self.c.disable(self.other, parent["name"], 2)

    def test_disable_preserves_history_but_stops_recommendation_and_reseed(self):
        before = self.learn()
        frappe.set_user("Administrator")
        parent = next(p for p in self.c.tree(self.workspace) if any(c["name"] == "ค่าไฟฟ้า" for c in p["children"]))
        self.c.disable(self.workspace, parent["id"], parent["revision"])
        self.assertEqual(self.c.disable(self.workspace, parent["id"], parent["revision"])["disabled"], True)
        count = frappe.db.count(self.c.CATEGORY, {"workspace": self.workspace})
        self.c.initialize(self.workspace)
        self.assertEqual(frappe.db.count(self.c.CATEGORY, {"workspace": self.workspace}), count)
        frappe.set_user(self.actor)
        self.assertEqual(self.s.read(before["name"]).pages, self.s.encode(before["pages"]))
        self.assertEqual(self.create()["pages"][0]["fields"]["category"], "ค่าน้ำมันเชื้อเพลิง")

    def test_empty_active_tree_does_not_revive_disabled_memory(self):
        frappe.set_user("Administrator")
        parent = self.c.save(self.workspace, "Custom", request_key="custom")
        self.c.disable(self.workspace, parent["name"], 1)
        frappe.set_user(self.actor)
        self.c.learn(self.workspace, None, [{"fields": {"seller_name": "PTT", "category": "Custom"}}])
        self.assertEqual(self.create()["pages"][0]["fields"]["category"], "")

    def test_restore_parent_does_not_silently_restore_children(self):
        frappe.set_user("Administrator")
        parent = self.c.save(self.workspace, "Parent", request_key="parent")
        child = self.c.save(self.workspace, "Child", parent=parent["name"], request_key="child")
        self.c.disable(self.workspace, parent["name"], 1)
        with self.assertRaises(frappe.ValidationError):
            self.c.set_enabled(self.workspace, child["name"], True, 2)
        self.c.set_enabled(self.workspace, parent["name"], True, 2)
        self.assertEqual(self.c.tree(self.workspace)[0]["children"], [])
        self.c.set_enabled(self.workspace, child["name"], True, 2)
        self.assertEqual(len(self.c.tree(self.workspace)[0]["children"]), 1)

    def test_custom_memory_and_invalid_old_memory(self):
        frappe.set_user("Administrator")
        self.c.save(self.workspace, "Custom", request_key="custom")
        frappe.set_user(self.actor)
        self.c.learn(self.workspace, None, [{"fields": {"seller_name": "PTT", "category": "Custom"}}])
        self.assertEqual(self.create()["pages"][0]["fields"]["category"], "Custom")
        self.c.learn(self.workspace, None, [{"fields": {"seller_name": "PTT", "category": "Unknown"}}])
        self.assertEqual(self.create()["pages"][0]["fields"]["category"], "")

    def test_permissions_feature_site_and_revoked_client(self):
        with self.assertRaises(frappe.PermissionError):
            self.c.initialize(self.workspace)
        frappe.set_user(self.reader)
        self.assertEqual(self.c.tree(self.workspace), [])
        with self.assertRaises(frappe.PermissionError):
            self.c.learn(self.workspace, None, PAGES)
        frappe.set_user(self.outsider)
        with self.assertRaises(frappe.PermissionError):
            self.c.tree(self.workspace)
        frappe.set_user(self.actor)
        frappe.conf.pearnly_product = "erp"
        with self.assertRaises(frappe.PermissionError):
            self.c.tree(self.workspace)
        frappe.conf.pearnly_product = "cowork"
        frappe.conf.pearnly_features = []
        with self.assertRaises(frappe.PermissionError):
            self.c.tree(self.workspace)
        frappe.conf.update(self.conf)
        frappe.set_user("Administrator")
        frappe.delete_doc("User Permission", self.permission)
        frappe.set_user(self.actor)
        frappe.clear_cache()
        with self.assertRaises(frappe.PermissionError):
            self.create(client=self.client)

    def test_native_crud_list_and_share_cannot_bypass(self):
        self.learn()
        for kind in self.c.TYPES:
            name = frappe.db.get_value(kind, {"workspace": self.workspace}, "name")
            doc = frappe.get_doc(kind, name)
            for operation in (lambda: doc.check_permission("read"), lambda: frappe.get_list(kind),
                    lambda: doc.save(ignore_permissions=True), lambda: frappe.delete_doc(kind, name, ignore_permissions=True)):
                with self.assertRaises(frappe.PermissionError):
                    operation()
            doc.flags.cowork_category_write = True
            with self.assertRaises(frappe.PermissionError):
                doc.save(ignore_permissions=True)
            with self.assertRaises(frappe.PermissionError):
                self.c.reject_share(frappe._dict(share_doctype=kind))

    def test_tree_failure_rolls_back_seed_and_keeps_model_input(self):
        original = self.c._persist
        count = 0
        def fail(doc):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("synthetic")
            return original(doc)
        with patch.object(self.c, "_persist", side_effect=fail):
            row = self.create()
        self.assertEqual(row["pages"][0]["fields"]["category"], "model raw")
        self.assertEqual(frappe.db.count(self.c.CATEGORY, {"workspace": self.workspace}), 0)

    def test_learning_failure_does_not_undo_review_and_security_errors_propagate(self):
        row = self.create()
        pages = copy.deepcopy(row["pages"])
        pages[0]["fields"]["category"] = "ค่าไฟฟ้า"
        with patch.object(self.c, "_persist", side_effect=RuntimeError("synthetic")):
            edited = self.s.update_pages(row["name"], pages, 1)
        self.assertEqual(edited["record_version"], 2)
        self.assertEqual(edited["pages"], pages)
        self.assertEqual(frappe.db.count(self.c.MEMORY, {"workspace": self.workspace}), 0)
        for error in (frappe.PermissionError, frappe.QueryDeadlockError):
            with patch.object(self.c, "_memory", side_effect=error("synthetic")):
                with self.assertRaises(error):
                    self.create()

    def test_invalid_category_inputs_fail_without_writes(self):
        frappe.set_user("Administrator")
        for sort in (None, [], "bad", True, "1.5", 2**35):
            with self.assertRaises(frappe.ValidationError):
                self.c.save(self.workspace, "Name", request_key=uuid4().hex, sort=sort)
        for name in (None, "", " " * 3, "x" * 201):
            with self.assertRaises(frappe.ValidationError):
                self.c.save(self.workspace, name, request_key=uuid4().hex)
        self.assertEqual(frappe.db.count(self.c.CATEGORY, {"workspace": self.workspace}), 0)

    def test_rpc_conflict_retry_is_bounded_and_does_not_swallow_permissions(self):
        from pearnly_cowork.history import category_api
        with patch.object(self.c, "initialize", side_effect=[frappe.QueryDeadlockError("conflict"), []]) as call:
            self.assertEqual(category_api.initialize_categories(self.workspace), [])
            self.assertEqual(call.call_count, 2)
        with patch.object(self.c, "initialize", side_effect=frappe.QueryDeadlockError("conflict")) as call:
            with self.assertRaises(frappe.QueryDeadlockError):
                category_api.initialize_categories(self.workspace)
            self.assertEqual(call.call_count, 3)
        with patch.object(self.c, "initialize", side_effect=frappe.PermissionError("denied")) as call:
            with self.assertRaises(frappe.PermissionError):
                category_api.initialize_categories(self.workspace)
            self.assertEqual(call.call_count, 1)


def http_checks():
    from werkzeug.test import Client
    from werkzeug.wrappers import Response
    from frappe.app import application
    from pearnly_cowork.masters import api
    actor = f"cw-category-http-{uuid4().hex[:10]}@example.invalid"
    user(actor, ["Cowork Admin"])
    workspace = api.create_workspace("Category HTTP " + uuid4().hex)["name"]
    account = frappe.get_doc("User", actor)
    account.api_key, secret = uuid4().hex, uuid4().hex
    account.api_secret = secret
    account.save()
    site = frappe.local.site
    frappe.db.commit()
    frappe.destroy()
    client = Client(application, Response)
    headers = {"Authorization": f"token {account.api_key}:{secret}"}
    rpc = "/api/method/pearnly_cowork.history.category_api."
    def call(path, method="GET", data=None, auth=True):
        return client.open(path, base_url=f"http://{site}", method=method, json=data, headers=headers if auth else {})
    assert call(rpc + "category_tree?workspace=" + workspace).json["message"] == []
    result = call(rpc + "save_category", "POST", {"workspace": workspace, "label": "Custom", "request_key": "once"})
    assert result.status_code == 200, result.get_data(as_text=True)
    name = result.json["message"]["name"]
    assert len(call(rpc + "category_tree?workspace=" + workspace).json["message"]) == 1
    assert call(rpc + "initialize_categories?workspace=" + workspace).status_code == 403
    for endpoint in ("initialize_categories", "save_category", "disable_category", "set_category_enabled"):
        assert call(rpc + endpoint, "POST", {"workspace": workspace}, auth=False).status_code == 403
    assert call(rpc + "category_tree?workspace=" + workspace, auth=False).status_code == 403
    for kind in ("Cowork Invoice Category", "Cowork Supplier Category Memory"):
        assert call("/api/resource/" + kind).status_code == 403
    assert call("/api/resource/Cowork Invoice Category/" + name).status_code == 403
    assert call("/api/resource/Cowork Invoice Category/" + name, "PUT", {"label": "forged"}).status_code == 403
    assert call(rpc + "disable_category", "POST", {"workspace": workspace, "name": name, "expected_revision": 1}).status_code == 200
    assert call(rpc + "category_tree?workspace=" + workspace).json["message"] == []
    connect()
    print(json.dumps({"http": "PASS", "requests": 15, "anonymous": [403] * 5, "native_crud": "denied"}))


def concurrency():
    from pearnly_cowork.masters import api
    from pearnly_cowork.history import categories
    workspace = api.create_workspace("Category concurrency " + uuid4().hex)["name"]
    frappe.db.commit()
    children = [subprocess.Popen([sys.executable, __file__, "--seed-worker", workspace],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            child.stdin.write("start\n")
            child.stdin.flush()
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr
            assert "seeded" in stdout, stdout
        expected = sum(1 + len(children) for _, children in categories.rules._PRESET)
        rows = categories.tree(workspace)
        assert sum(1 + len(row["children"]) for row in rows) == expected
        assert frappe.db.count(categories.CATEGORY, {"workspace": workspace}) == expected
        print(json.dumps({"concurrent_first_seeds": 2, "category_count": expected, "result": "PASS"}))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == "__main__":
    connect()
    try:
        if "--install-fresh" in sys.argv:
            BASE["install_fresh"](site=FRESH_SITE)
        elif "--snapshot" in sys.argv:
            print(json.dumps(snapshot(), sort_keys=True))
        elif "--http" in sys.argv:
            http_checks()
        elif "--concurrency" in sys.argv:
            concurrency()
        elif "--seed-worker" in sys.argv:
            from pearnly_cowork.history import categories, category_api
            workspace = sys.argv[-1]
            assert (frappe.db.get_value("Cowork Workspace", workspace, "display_name") or "").startswith("Category concurrency ")
            # Establish an old REPEATABLE READ snapshot before competing for the lock.
            assert frappe.db.count(categories.CATEGORY, {"workspace": workspace}) == 0
            print("ready", flush=True)
            assert sys.stdin.readline().strip() == "start"
            category_api.initialize_categories(workspace)
            frappe.db.commit()
            print("seeded", flush=True)
        elif "--prepare-upgrade" in sys.argv:
            ClassificationChecks.setUpClass()
            case = ClassificationChecks()
            case.setUp()
            case.learn()
            frappe.set_user("Administrator")
            custom = case.c.save(case.workspace, "Upgrade custom", request_key="upgrade")
            case.c.disable(case.workspace, custom["name"], 1)
            frappe.db.commit()
            print(json.dumps(snapshot(), sort_keys=True))
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ClassificationChecks))
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, "db", None):
            frappe.db.rollback()
        frappe.destroy()
