"""开户后台的「识别引擎」（ADR-017）：开通计划与改引擎时重排的 set_ocr 步骤带对引擎。

Run with Frappe installed. No Site, database, credentials, network or tenant writes:
documents are plain objects and every Frappe/database collaborator is replaced.
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from pearnly_control import provisioning
from pearnly_control.pearnly_control.doctype.pearnly_deployment.pearnly_deployment import PearnlyDeployment


class Rejected(Exception):
    pass


def deployment(product="cowork", engine="claude", **extra):
    values = dict(name="DEP-1", issue="ISS-1", site_name="t.pearnly.com", bench_group=product, product=product,
                  ocr_engine=engine)
    values.update(extra)
    dep = SimpleNamespace(**values)
    dep.get = lambda key, default=None: getattr(dep, key, default)
    return dep


class Base(unittest.TestCase):
    def setUp(self):
        self.throw = Mock(side_effect=Rejected)
        for attribute, value in {"throw": self.throw, "_": lambda x: x, "db": Mock()}.items():
            p = patch.object(frappe, attribute, value, create=True)
            p.start(); self.addCleanup(p.stop)
        p = patch("pearnly_control.provisioning._", lambda x: x); p.start(); self.addCleanup(p.stop)


class Payload(Base):
    def test_cowork_carries_its_engine_and_defaults_to_the_current_reading(self):
        self.assertEqual(provisioning._ocr_payload(deployment()),
                         {"site": "t.pearnly.com", "group": "cowork", "engine": "claude"})
        self.assertEqual(provisioning._ocr_payload(deployment(engine=""))["engine"], "gemini")
        self.assertEqual(provisioning._ocr_payload(deployment(engine=None))["engine"], "gemini")

    def test_other_products_carry_no_engine(self):
        self.assertEqual(provisioning._ocr_payload(deployment(product="erp")), {"site": "t.pearnly.com", "group": "erp"})

    def test_unknown_engine_is_refused(self):
        with self.assertRaises(Rejected):
            provisioning._ocr_payload(deployment(engine="gpt"))


class Requeue(Base):
    def queue(self, step_status="done", engine="claude"):
        step = SimpleNamespace(deployment="DEP-1", action="set_ocr", status=step_status, payload="{}")
        req = SimpleNamespace(steps=[step], save=Mock())
        frappe.db.get_value.side_effect = ["cloud", "TEN-1", "active", "REQ-1"]
        with patch.object(frappe, "get_doc", return_value=req), \
             patch.object(provisioning.infra, "is_cloud", return_value=True):
            provisioning.queue_ocr_engine(deployment(engine=engine))
        return step, req

    def test_changing_the_engine_requeues_set_ocr_with_a_restart(self):
        step, req = self.queue()
        self.assertEqual(json.loads(step.payload),
                         {"site": "t.pearnly.com", "group": "cowork", "engine": "claude", "restart": True})
        self.assertEqual((step.status, req.status), ("pending", "pending"))
        req.save.assert_called_once()

    def test_a_running_step_is_not_overwritten(self):
        with self.assertRaises(Rejected):
            self.queue(step_status="running")

    def test_non_cowork_and_local_targets_are_left_alone(self):
        with patch.object(frappe, "get_doc") as get_doc:
            provisioning.queue_ocr_engine(deployment(product="erp"))
            frappe.db.get_value.side_effect = ["local"]
            with patch.object(provisioning.infra, "is_cloud", return_value=False):
                provisioning.queue_ocr_engine(deployment())
        get_doc.assert_not_called()


class Controller(Base):
    def test_only_an_engine_change_on_an_existing_cowork_deployment_requeues(self):
        for product, inserting, changed, expected in (("cowork", False, True, 1), ("cowork", True, True, 0),
                                                      ("cowork", False, False, 0), ("erp", False, True, 0)):
            doc = SimpleNamespace(product=product, flags=SimpleNamespace(in_insert=inserting),
                                  has_value_changed=lambda field, changed=changed: changed and field == "ocr_engine")
            with patch.object(provisioning, "queue_ocr_engine") as queue:
                PearnlyDeployment.on_update(doc)
            self.assertEqual(queue.call_count, expected, (product, inserting, changed))


if __name__ == "__main__":
    unittest.main()
