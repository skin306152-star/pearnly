"""Synthetic native connection tests: dedicated Cowork lab only, zero outbound IO.

Creates labelled lab fixtures. Native access hooks must be installed by root.
Does not start/migrate a Site or act as an ERP collector.
"""
from copy import deepcopy
from pathlib import Path
import json
import runpy
import unittest
import uuid
from unittest.mock import patch

import frappe
from frappe.utils.password import get_decrypted_password

BASE = runpy.run_path(str(Path(__file__).with_name("cowork-backend-lab-verify.py")))


class NativeConnections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_cowork.connections import api, service, access, model
        from pearnly_cowork.masters import api as masters
        from pearnly_cowork.targets import freeze_target
        cls.api, cls.service, cls.access, cls.model, cls.freeze = api, service, access, model, staticmethod(freeze_target)
        cls.saved_features = deepcopy(frappe.conf.pearnly_features)
        frappe.conf.pearnly_features = ["intake", "master_data", "push_log"]
        cls.suffix=uuid.uuid4().hex[:10]
        cls.manager="cw-conn-admin-"+cls.suffix+"@example.invalid"
        cls.actor="cw-conn-actor-"+cls.suffix+"@example.invalid"
        cls.outsider="cw-conn-out-"+cls.suffix+"@example.invalid"
        cls.log_reader="cw-conn-log-"+cls.suffix+"@example.invalid"
        if not frappe.db.exists("Role", "Cowork Push Log"):
            frappe.get_doc({"doctype":"Role","role_name":"Cowork Push Log"}).insert()
        for user, roles in ((cls.manager,["Cowork Admin"]),(cls.actor,["Cowork Intake"]),
                            (cls.outsider,["Cowork Intake"]),(cls.log_reader,["Cowork Push Log"])):
            BASE["user"](user,roles)
        cls.workspace=masters.create_workspace("Synthetic connection "+cls.suffix)["name"]
        cls.foreign=masters.create_workspace("Synthetic foreign connection "+cls.suffix)["name"]
        cls.client=masters.create_client(cls.workspace,"Synthetic buyer "+cls.suffix)["name"]
        for user in (cls.actor,cls.log_reader):
            BASE["grant"](user,"Cowork Workspace",cls.workspace)
        BASE["grant"](cls.outsider,"Cowork Workspace",cls.foreign)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.conf.pearnly_features=cls.saved_features

    def setUp(self):
        frappe.set_user(self.manager)
        frappe.conf.pearnly_features=["intake","master_data","push_log"]
        self.product=frappe.conf.pearnly_product

    def tearDown(self):
        frappe.db.rollback()
        frappe.conf.pearnly_product=self.product
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def create(self, **changes):
        args={"workspace":self.workspace,"adapter":"express","request_key":uuid.uuid4().hex,
              "label":"Synthetic Express","config":{"account_set":r"C:\BOOK","express_root":r"D:\APP"},
              "users":[self.manager,self.actor,self.log_reader]}
        args.update(changes)
        return self.api.configure(**args)

    def load(self,row):
        return self.service.read_for_push(row["name"],self.workspace)

    def frozen(self,loaded,**kwargs):
        return self.freeze(loaded.endpoint,access=loaded.access,evidence=loaded.evidence,**kwargs)

    def observation(self):
        return {"adapter":"express","observed_at":"2026-09-22T01:00:00Z",
                "collector":{"kind":"synthetic-lab"},"account_sets":[
                  {"source_id":r"C:\BOOK","attributes":{"path":r"C:\BOOK","root":r"D:\APP","writable":True}},
                  {"source_id":r"E:\OTHER","attributes":{"path":r"E:\OTHER","root":r"D:\APP","writable":True}}],
                "masters":{},"form_schema":{"fields":[]},"capabilities":{"actions":[]}}

    def preparation(self):
        return {"complete":True,"account_set":r"C:\BOOK","root_key":r"D:\APP",
                "mappings":{"accounts":[],"clients":[]},"supplier_profiles":{},"bank_index":[],"reported_config":{}}

    def install(self,row,prepared=None):
        refresh=self.api.request_refresh(row["name"],uuid.uuid4().hex)
        installed=self.service.install_snapshot(row["name"],self.observation(),refresh["request_id"],row["revision"],preparation=prepared)
        return refresh,installed

    def test_configure_idempotent_and_credentials_are_native_encrypted_and_never_returned(self):
        key=uuid.uuid4().hex
        row=self.create(request_key=key,credentials={"username":"synthetic-user","password":"synthetic-password"})
        again=self.create(request_key=key,credentials={"username":"synthetic-user","password":"synthetic-password"})
        self.assertEqual(row["name"],again["name"])
        self.assertNotIn("synthetic-password",json.dumps(row))
        self.assertNotIn("synthetic-user",json.dumps(row))
        self.assertEqual(get_decrypted_password(self.service.CONNECTION,row["name"],"credential_password"),"synthetic-password")
        self.assertEqual(frappe.db.count(self.service.CONNECTION,{"name":row["name"]}),1)
        with self.assertRaises(frappe.ValidationError): self.create(request_key=key,label="Different")

    def test_full_config_native_password_survives_update_and_old_revision_is_rejected(self):
        row=self.create(credentials={"password":"synthetic-password"})
        updated=self.api.configure(self.workspace,"express",uuid.uuid4().hex,"Changed",row["config"],
                    users=row["users"],name=row["name"],expected_revision=1)
        self.assertEqual(updated["revision"],2)
        self.assertEqual(get_decrypted_password(self.service.CONNECTION,row["name"],"credential_password"),"synthetic-password")
        with self.assertRaises(frappe.ValidationError):
            self.api.configure(self.workspace,"express",uuid.uuid4().hex,"Changed",row["config"],name=row["name"],expected_revision=1)

    def test_current_explicit_assignee_and_exact_company_client_scope_are_required(self):
        row=self.create()
        frappe.set_user(self.actor)
        loaded=self.load(row)
        self.assertEqual(loaded.access.workspace_client_id,self.workspace)
        self.assertEqual(loaded.endpoint["workspace_client_id"],self.workspace)
        with self.assertRaises(frappe.PermissionError): self.service.read_for_push(row["name"],self.workspace,self.client)
        frappe.set_user(self.outsider)
        with self.assertRaises(frappe.PermissionError): self.load(row)
        frappe.set_user(self.manager)
        row=self.create(users=[self.actor])
        self.assertEqual(self.api.detail(row["name"])["name"],row["name"])
        with self.assertRaises(frappe.PermissionError): self.load(row)

    def test_log_only_role_reads_but_cannot_prepare_or_refresh(self):
        row=self.create();frappe.set_user(self.log_reader)
        self.assertEqual(self.service.read_for_log(row["name"])["name"],row["name"])
        with self.assertRaises(frappe.PermissionError):self.load(row)
        with self.assertRaises(frappe.PermissionError):self.api.request_refresh(row["name"],uuid.uuid4().hex)

    def test_admin_with_intake_only_can_view_assigned_connections(self):
        row=self.create()
        frappe.conf.pearnly_features=["intake"]
        self.assertEqual(self.api.detail(row["name"])["name"],row["name"])
        self.assertEqual([r["name"] for r in self.api.records(self.workspace)],[row["name"]])
        self.assertEqual(self.api.targets(row["name"])["status"],"missing")
        with self.assertRaises(frappe.PermissionError):self.create()

    def test_readonly_directory_reports_pending_and_only_safe_current_account_fields(self):
        row=self.create();request=self.api.request_refresh(row["name"],uuid.uuid4().hex)
        pending=self.api.targets(row["name"])
        self.assertEqual(pending["refresh_status"],"requested")
        self.assertEqual(pending["status"],"pending")
        self.assertEqual(pending["refresh_request_id"],request["request_id"])
        self.assertFalse(pending["collector_available"])
        observation=self.observation()
        observation["account_sets"][0]["attributes"]["extra_raw"]="do-not-expose"
        prep=self.preparation();prep["supplier_profiles"]={"0105555555555":{"default_payment":"cash"}}
        self.service.install_snapshot(row["name"],observation,request["request_id"],1,preparation=prep)
        frappe.set_user(self.actor)
        directory=self.api.targets(row["name"])
        self.assertEqual(directory["status"],"fresh")
        self.assertEqual(directory["revision"],1)
        self.assertEqual(len(directory["account_sets"]),2)
        text=json.dumps(directory)
        for hidden in ("do-not-expose","supplier_profiles","default_payment","bank_index","preparation"):
            self.assertNotIn(hidden,text)

    def test_disabled_user_feature_product_and_connection_are_rechecked(self):
        row=self.create();frappe.set_user(self.actor)
        for change in ("feature","product","user","connection"):
            with self.subTest(change=change):
                point="conn_revoke";frappe.db.savepoint(point)
                features=deepcopy(frappe.conf.pearnly_features)
                if change=="feature":frappe.conf.pearnly_features=[]
                if change=="product":frappe.conf.pearnly_product="erp"
                if change=="user":frappe.db.set_value("User",self.actor,"enabled",0)
                if change=="connection":frappe.db.set_value(self.service.CONNECTION,row["name"],"enabled",0)
                with self.assertRaises(frappe.PermissionError):self.load(row)
                frappe.db.rollback(save_point=point)
                frappe.conf.pearnly_features=features;frappe.conf.pearnly_product=self.product

    def test_refresh_request_is_durable_pending_idempotent_and_never_fake_success(self):
        row=self.create();key=uuid.uuid4().hex
        first=self.api.request_refresh(row["name"],key)
        self.assertEqual(self.api.request_refresh(row["name"],key),first)
        self.assertEqual(first["status"],"requested")
        self.assertFalse(first["collector_available"])
        self.assertEqual(self.api.refresh_status(row["name"],first["request_id"])["status"],"requested")
        loaded=self.load(row);target=self.frozen(loaded)
        self.assertFalse(target.proof_required)
        with self.assertRaises(frappe.ValidationError):self.service.preparation_for_target(loaded,target)

    def test_snapshot_normalization_atomic_evidence_and_exact_revision(self):
        row=self.create();refresh,installed=self.install(row,self.preparation())
        loaded=self.load(row)
        target=self.frozen(loaded,account_set_key=r"E:\OTHER",request_id=refresh["request_id"],revision=installed["revision"])
        self.assertTrue(target.proof_required)
        self.assertEqual(target.catalog_projection_revision,1)
        default=self.frozen(loaded)
        self.assertEqual(self.service.preparation_for_target(loaded,default)["mappings"],{"accounts":[],"clients":[]})
        with self.assertRaises(frappe.ValidationError):self.service.preparation_for_target(loaded,target)

    def test_identical_projection_does_not_increment_and_changed_preparation_does(self):
        row=self.create();_,first=self.install(row,self.preparation())
        _,second=self.install(row,self.preparation())
        self.assertEqual(first["snapshot_id"],second["snapshot_id"])
        changed=self.preparation();changed["mappings"]["accounts"].append({"erp_type":"express","pearnly_category":"goods","erp_code":"5001"})
        _,third=self.install(row,changed)
        self.assertEqual(third["revision"],2)

    def test_superseded_refresh_and_config_revision_cannot_publish(self):
        row=self.create();old=self.api.request_refresh(row["name"],uuid.uuid4().hex)
        self.service.install_snapshot(row["name"],self.observation(),old["request_id"],1)
        fresh=self.api.request_refresh(row["name"],uuid.uuid4().hex)
        self.assertNotEqual(old["request_id"],fresh["request_id"])
        with self.assertRaises(frappe.ValidationError):self.service.install_snapshot(row["name"],self.observation(),old["request_id"],1)
        self.api.configure(self.workspace,"express",uuid.uuid4().hex,"Changed",row["config"],users=row["users"],name=row["name"],expected_revision=1)
        with self.assertRaises(frappe.ValidationError):self.service.install_snapshot(row["name"],self.observation(),fresh["request_id"],1)

    def test_requested_refresh_coalesces_and_historical_aliases_replay_original_result(self):
        row=self.create();key1,key2,key3=[uuid.uuid4().hex for _ in range(3)]
        first=self.api.request_refresh(row["name"],key1)
        second=self.api.request_refresh(row["name"],key2)
        self.assertEqual(first["request_id"],second["request_id"])
        self.assertEqual(frappe.db.count(self.service.REFRESH,{"connection":row["name"]}),1)
        persisted=json.loads(frappe.db.get_value(self.service.CONNECTION,row["name"],"refresh_request_aliases"))
        self.assertEqual(len(persisted),2)
        self.assertEqual({v["request_id"] for v in persisted.values()},{first["request_id"]})
        self.service.install_snapshot(row["name"],self.observation(),first["request_id"],1)
        newest=self.api.request_refresh(row["name"],key3)
        self.assertNotEqual(first["request_id"],newest["request_id"])
        for key in (key1,key2):
            replay=self.api.request_refresh(row["name"],key)
            self.assertEqual((replay["request_id"],replay["status"]),(first["request_id"],"succeeded"))
        self.assertEqual(self.api.targets(row["name"])["refresh_request_id"],newest["request_id"])
        self.assertEqual(frappe.db.count(self.service.REFRESH,{"connection":row["name"]}),2)

    def test_refresh_new_revision_does_not_coalesce_and_old_keys_do_not_rebind(self):
        row=self.create();key1,key2=[uuid.uuid4().hex for _ in range(2)]
        old=self.api.request_refresh(row["name"],key1)
        self.assertEqual(self.api.request_refresh(row["name"],key2)["request_id"],old["request_id"])
        self.api.configure(self.workspace,"express",uuid.uuid4().hex,"Changed",row["config"],
                           users=row["users"],name=row["name"],expected_revision=1)
        fresh=self.api.request_refresh(row["name"],uuid.uuid4().hex)
        self.assertNotEqual(old["request_id"],fresh["request_id"])
        self.assertEqual(frappe.db.get_value(self.service.REFRESH,fresh["request_id"],"connection_revision"),2)
        for key in (key1,key2):
            with self.assertRaises(frappe.ValidationError):self.api.request_refresh(row["name"],key)
        self.assertEqual(self.api.targets(row["name"])["refresh_request_id"],fresh["request_id"])
        with self.assertRaises(frappe.ValidationError):self.service.install_snapshot(row["name"],self.observation(),old["request_id"],2)

    def test_refresh_scope_separates_requests_and_same_key_cannot_change_scope(self):
        row=self.create();key=uuid.uuid4().hex
        endpoint=self.api.request_refresh(row["name"],key)
        account=self.api.request_refresh(row["name"],uuid.uuid4().hex,account_set_key=r"C:\BOOK")
        self.assertNotEqual(endpoint["request_id"],account["request_id"])
        with self.assertRaises(frappe.ValidationError):self.api.request_refresh(row["name"],key,account_set_key=r"C:\BOOK")

    def test_incomplete_source_context_and_stale_loaded_values_cannot_prepare(self):
        row=self.create();value=self.preparation();value["complete"]=False
        self.install(row,value);loaded=self.load(row);target=self.frozen(loaded)
        with self.assertRaises(frappe.ValidationError):self.service.preparation_for_target(loaded,target)
        self.api.configure(self.workspace,"express",uuid.uuid4().hex,"Changed",row["config"],users=row["users"],name=row["name"],expected_revision=1)
        with self.assertRaises(frappe.ValidationError):self.service.preparation_for_target(loaded,target)

    def test_exact_account_snapshot_supersedes_endpoint_preparation(self):
        row=self.create();self.install(row,self.preparation())
        request=self.api.request_refresh(row["name"],uuid.uuid4().hex,account_set_key="c:/book/")
        observation=self.observation();observation["account_set_key"]=r"C:\BOOK"
        value=self.preparation();value["bank_index"]=[{"direction":"OUT","amount":"107.00","tx_date":"2026-09-22"}]
        self.service.install_snapshot(row["name"],observation,request["request_id"],1,preparation=value)
        loaded=self.load(row)
        self.assertEqual(self.service.preparation_for_target(loaded,self.frozen(loaded))["bank_index"],value["bank_index"])

    def test_pending_or_incomplete_exact_account_never_falls_back_to_endpoint(self):
        row=self.create();self.install(row,self.preparation())
        request=self.api.request_refresh(row["name"],uuid.uuid4().hex,account_set_key=r"C:\BOOK")
        loaded=self.load(row)
        with self.assertRaises(frappe.ValidationError):self.service.preparation_for_target(loaded,self.frozen(loaded))
        observation=self.observation();observation["account_set_key"]=r"C:\BOOK"
        value=self.preparation();value["complete"]=False
        self.service.install_snapshot(row["name"],observation,request["request_id"],1,preparation=value)
        loaded=self.load(row)
        with self.assertRaises(frappe.ValidationError):self.service.preparation_for_target(loaded,self.frozen(loaded))

    def test_native_create_read_write_list_and_share_are_denied(self):
        row=self.create()
        doc=frappe.get_doc(self.service.CONNECTION,row["name"])
        with self.assertRaises(frappe.PermissionError):doc.check_permission("read")
        with self.assertRaises(frappe.PermissionError):doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):frappe.get_list(self.service.CONNECTION,pluck="name")
        for dt in self.access.TYPES:
            with self.assertRaises(frappe.PermissionError):frappe.get_doc({"doctype":dt,"record_key":uuid.uuid4().hex}).insert(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc({"doctype":"DocShare","share_doctype":self.service.CONNECTION,"share_name":row["name"],"user":self.outsider,"read":1}).insert(ignore_permissions=True)

    def test_legacy_share_cannot_restore_generic_list_or_direct_read(self):
        row=self.create()
        share=frappe.get_doc({"doctype":"DocShare","share_doctype":self.service.CONNECTION,"share_name":row["name"],"user":self.outsider,"read":1})
        share.name=uuid.uuid4().hex;share.db_insert()
        frappe.set_user(self.outsider)
        with self.assertRaises(frappe.PermissionError):frappe.get_list(self.service.CONNECTION,pluck="name")
        with self.assertRaises(frappe.PermissionError):frappe.get_doc(self.service.CONNECTION,row["name"]).check_permission("read")
        with self.assertRaises(frappe.PermissionError):self.api.detail(row["name"])

    def test_snapshot_installer_and_credentials_are_not_whitelisted(self):
        self.assertNotIn(self.service.install_snapshot,frappe.whitelisted)
        self.assertNotIn(self.service.credentials_for_use,frappe.whitelisted)
        self.assertNotIn(self.service.read_for_push,frappe.whitelisted)

    def test_rpc_only_retries_local_conflict_with_rollback_and_same_request(self):
        # Bounded injected failures prove the RPC boundary, not all concurrency.
        with patch.object(self.service,"configure",side_effect=frappe.QueryDeadlockError("synthetic")) as called:
            with patch.object(frappe.db,"rollback",wraps=frappe.db.rollback) as rollback:
                with self.assertRaises(frappe.QueryDeadlockError):self.create()
                self.assertEqual(called.call_count,3)
                self.assertEqual(rollback.call_count,3)
        with patch.object(self.service,"configure",side_effect=frappe.PermissionError("synthetic")) as called:
            with patch.object(frappe.db,"rollback",wraps=frappe.db.rollback) as rollback:
                with self.assertRaises(frappe.PermissionError):self.create()
                self.assertEqual(called.call_count,1)
                self.assertEqual(rollback.call_count,0)
        real=self.service.configure; attempts=[]
        def partial_then_retry(*args,**kwargs):
            result=real(*args,**kwargs)
            attempts.append(result["name"])
            if len(attempts)==1:raise frappe.QueryDeadlockError("synthetic after local insert")
            return result
        with patch.object(self.service,"configure",side_effect=partial_then_retry):
            row=self.create(request_key="same-after-local-conflict-"+uuid.uuid4().hex)
        self.assertEqual(attempts,[row["name"],row["name"]])
        self.assertEqual(frappe.db.count(self.service.CONNECTION,{"name":row["name"]}),1)


if __name__=="__main__":
    BASE["connect"]()
    try:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeConnections))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.db.rollback();frappe.destroy()
