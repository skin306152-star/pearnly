"""Offline first-use email contracts. No SMTP, database, or real account writes."""
import importlib.util
from pathlib import Path
import sys
import json
from types import SimpleNamespace as NS, ModuleType
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
frappe = ModuleType("frappe")
frappe._ = lambda text, **kw: text
frappe.validate_and_sanitize_search_inputs = lambda fn: fn
frappe.whitelist = lambda **kw: lambda fn: fn
sys.modules["frappe"] = frappe
sys.path.insert(0, str(ROOT / "apps/pearnly_control"))
from pearnly_control.onboarding import welcome


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.issue = NS(name="ISS-TEST", status="issued", identity_subject="identity", target="cloud",
            tenant="TENANT-TEST", tenant_name='<Company & Co>', account_email="user@example.invalid",
            account_username="example", apps=[NS(app="dms", domain="company.example.com")],
            get_password=Mock(return_value="secret<&>"))
        self.fields = {}
        self.issue.get = lambda k: self.fields.get(k)
        self.request = NS(name="PR-TEST", issue=self.issue.name, status="done", has_value_changed=lambda k: True,
            steps=[NS(action=a, deployment="DEP-TEST", status="done")
                   for a in ("new_site", "set_oidc", "bind_user", "publish_site", "mark_active")] + [
                NS(action="install_app", deployment="DEP-TEST", status="done", step_key="DEP-TEST:install_core",
                   payload=json.dumps({"site": "company.example.com", "app": "pearnly_core"})),
                NS(action="install_app", deployment="DEP-TEST", status="done", step_key="DEP-TEST:install_product",
                   payload=json.dumps({"site": "company.example.com", "app": "pearnly_dms"})),
            ])
        self.account = NS(name="Pearnly", enable_outgoing=1, email_id=welcome.SENDER, always_bcc="")
        frappe.conf = {"pearnly_welcome_email_account": "Pearnly"}
        frappe.db = NS(sql=Mock(),get_value=Mock(return_value="DEP-TEST"),
            set_value=Mock(side_effect=self.set_value),savepoint=Mock(),rollback=Mock())
        frappe.get_doc = Mock(side_effect=lambda dt,name: self.issue if dt==welcome.ISSUE else self.account)
        frappe.get_app_path = lambda app,*parts: str(ROOT / 'apps' / app / app / Path(*parts))
        frappe.sendmail = Mock(return_value=NS(name="QUEUE-TEST",email_account="Pearnly"))
        welcome.require_cloud_target = Mock()

    def set_value(self, doctype, name, fields, *args, **kwargs):
        if isinstance(fields,dict):
            self.fields.update(fields)
            for k,v in fields.items():setattr(self.issue,k,v)
        else: self.fields[fields] = args[0]

    def test_completion_queues_once_and_redacts(self):
        welcome.request_completed(self.request)
        welcome.request_completed(self.request)
        self.assertEqual(frappe.sendmail.call_count,1)
        args=frappe.sendmail.call_args.kwargs
        self.assertEqual(args['recipients'],['user@example.invalid'])
        self.assertEqual(args['sender'],'Pearnly <hello@pearnly.com>')
        self.assertEqual(args['reply_to'],'hello@pearnly.com')
        self.assertTrue(args['delayed']);self.assertTrue(args['redact_message_after_send'])
        self.assertEqual(args['args']['language'],'th')
        self.assertEqual(args['args']['products'][0]['url'],'https://company.example.com/')
        self.assertNotIn('secret',str(frappe.db.set_value.call_args_list))

    def test_failures_do_not_change_provisioning(self):
        frappe.sendmail.side_effect=RuntimeError('sensitive detail')
        welcome.request_completed(self.request)
        self.assertEqual(self.request.status,'done')
        frappe.db.rollback.assert_called_once_with(save_point='welcome_email')
        self.assertIn('welcome_email_error',self.fields)
        self.assertNotIn('sensitive detail',self.fields['welcome_email_error'])

    def test_skip_noncompletion_and_historical_refresh(self):
        for status in ['pending','running','partial','failed']:
            self.request.status=status;welcome.request_completed(self.request)
        self.request.status='done';self.request.has_value_changed=lambda k:False
        welcome.request_completed(self.request);frappe.sendmail.assert_not_called()

    def test_empty_and_teardown_do_not_send(self):
        for steps in [[],[NS(action='drop_site')],[NS(action='purge_control')]]:
            self.request.steps=steps;welcome.request_completed(self.request)
        frappe.sendmail.assert_not_called()

    def test_required_steps_cannot_be_skipped(self):
        self.request.steps[0].status='skipped'
        with self.assertRaises(ValueError):welcome.queue_for_request(self.request)
        frappe.sendmail.assert_not_called()

    def test_missing_or_wrong_installation_cannot_send(self):
        for field, value in [("status", "skipped"), ("deployment", "OTHER"),
                             ("step_key", "DEP-TEST:install_core"),
                             ("payload", '{"site":"company.example.com","app":"pearnly_core"}'),
                             ("payload", '{"site":"other.example.com","app":"pearnly_dms"}')]:
            step = self.request.steps[-1]
            previous = getattr(step, field)
            setattr(step, field, value)
            with self.assertRaises(ValueError):
                welcome.queue_for_request(self.request)
            setattr(step, field, previous)
        frappe.sendmail.assert_not_called()

    def test_inactive_wrong_owner_or_missing_deployment_blocks(self):
        frappe.db.get_value.return_value=None
        with self.assertRaises(ValueError):welcome.queue_for_request(self.request)
        filters=frappe.db.get_value.call_args.args[1]
        self.assertEqual(filters['issue'],'ISS-TEST');self.assertEqual(filters['tenant'],'TENANT-TEST')
        self.assertEqual(filters['status'],'active')

    def test_local_target_blocks(self):
        welcome.require_cloud_target.side_effect=ValueError('local')
        with self.assertRaises(ValueError):welcome.queue_for_request(self.request)
        frappe.sendmail.assert_not_called()

    def test_bad_domain_blocks(self):
        for domain in ['javascript:alert(1)','https://a.example.com','a.example.com/','a.example.com\" onclick=']:
            self.issue.apps[0].domain=domain
            with self.assertRaises(ValueError):welcome.queue_for_request(self.request)

    def test_bcc_and_wrong_sender_block(self):
        self.account.always_bcc='someone@example.invalid'
        with self.assertRaises(ValueError):welcome.queue_for_request(self.request)
        self.account.always_bcc='';self.account.email_id='old@gmail.com'
        with self.assertRaises(ValueError):welcome.queue_for_request(self.request)
        frappe.sendmail.assert_not_called()

    def test_language_mapping(self):
        for supplied,expected in [('zh-CN','zh-CN'),('zh','zh-CN'),('en','en'),('ja','ja'),('unknown','th')]:
            self.fields.clear();self.fields['welcome_email_language']=supplied
            welcome.queue_for_request(self.request)
            self.assertEqual(frappe.sendmail.call_args.kwargs['args']['language'],expected)

    def test_multiple_products_keep_separate_domains(self):
        self.issue.apps.append(NS(app='erp',domain='erp.example.com'))
        frappe.db.get_value.side_effect = lambda dt, filters, field: "DEP-ERP" if filters.get("product") == "erp" else "DEP-TEST"
        import copy
        extra = copy.deepcopy(self.request.steps)
        for step in extra:
            step.deployment = "DEP-ERP"
            if step.action == "install_app":
                step.step_key = step.step_key.replace("DEP-TEST", "DEP-ERP")
                payload = json.loads(step.payload)
                payload["site"] = "erp.example.com"
                if payload["app"] == "pearnly_dms":
                    payload["app"] = "pearnly_erp_portal"
                step.payload = json.dumps(payload)
        self.request.steps.extend(extra)
        welcome.queue_for_request(self.request)
        args=frappe.sendmail.call_args.kwargs
        self.assertEqual(args['args']['product_name'],'Pearnly')
        self.assertEqual(len(args['args']['products']),2)

    def test_persisted_receipt_prevents_password_reread(self):
        self.fields['welcome_email_queue']='previous-queue'
        self.issue.welcome_email_queue='previous-queue'
        self.assertEqual(welcome.queue_for_request(self.request),'previous-queue')
        self.issue.get_password.assert_not_called();frappe.sendmail.assert_not_called()

    def test_retry_requires_operator(self):
        frappe.only_for=Mock(side_effect=PermissionError)
        with self.assertRaises(PermissionError):welcome.retry_queue('ISS-TEST')
        frappe.sendmail.assert_not_called()

if __name__=='__main__':unittest.main()
