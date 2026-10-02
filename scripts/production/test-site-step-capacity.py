#!/usr/bin/env python3
"""No network: provisioning writes the shared OCR budget and Document AI regions from runtime.env."""
import importlib.util
import json
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[2] / "infrastructure/production/site-step.py"
spec = importlib.util.spec_from_file_location("site_step", path)
step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step)

REGIONS = [{"location": "asia-southeast1", "processor": "6c7dfffac937fcd9",
            "version": "pretrained-ocr-v2.1.1-2025-01-31", "rpm": 60},
           {"location": "us", "processor": "2a5bdf2779fcc187",
            "version": "pretrained-ocr-v2.1-2024-08-07", "rpm": 120}]


class Endpoints(unittest.TestCase):
    """数据库与 Redis 地址只靠 runtime.env（2026-10-02 可拆分自检）：不填就是同一台机器的服务名，填了就用填的。"""

    def test_defaults_are_the_compose_service_names(self):
        self.assertEqual(step.endpoints({}), {"db_host": "mariadb", "db_port": 3306,
                                              "cache": "redis://redis-cache:6379", "queue": "redis://redis-queue:6379"})

    def test_a_separate_database_or_redis_host_is_configuration_only(self):
        hosts = step.endpoints({"PEARNLY_DB_HOST": "10.0.0.5", "PEARNLY_DB_PORT": "3307",
                                "PEARNLY_REDIS_CACHE_URL": "redis://10.0.0.6:6379/", "PEARNLY_REDIS_QUEUE_URL": "redis://10.0.0.7:6380"})
        self.assertEqual(hosts, {"db_host": "10.0.0.5", "db_port": 3307,
                                 "cache": "redis://10.0.0.6:6379", "queue": "redis://10.0.0.7:6380"})

    def test_nothing_writes_the_service_names_back(self):
        for name in ("site-step.py", "bootstrap-billing.py"):
            source = (path.parent / name).read_text()
            self.assertNotIn('db_host="mariadb"', source, name)
            self.assertNotIn('"db_host": "mariadb"', source, name)
            self.assertNotIn('"redis://redis-queue:6379/6"', source, name)


class OcrBudget(unittest.TestCase):
    def test_without_runtime_value_keeps_the_agreed_budget(self):
        self.assertEqual(step.ocr_budget({}), {"concurrency": 8, "tenant_concurrency": 3, "rpm": 120})
        self.assertEqual(step.ocr_budget({"PEARNLY_OCR_BUDGET": "  "}), step.OCR_BUDGET)

    def test_runtime_value_overrides_only_the_given_keys(self):
        budget = step.ocr_budget({"PEARNLY_OCR_BUDGET": '{"concurrency": 24, "rpm": 600}'})
        self.assertEqual(budget, {"concurrency": 24, "tenant_concurrency": 3, "rpm": 600})
        self.assertEqual(step.OCR_BUDGET["rpm"], 120)

    def test_same_runtime_gives_every_product_the_same_policy(self):
        runtime = {"PEARNLY_OCR_BUDGET": '{"concurrency": 16, "tenant_concurrency": 4}'}
        self.assertEqual(json.dumps(step.ocr_budget(runtime), sort_keys=True),
                         json.dumps(step.ocr_budget(dict(runtime)), sort_keys=True))

    def test_rejects_values_the_budget_would_refuse_at_runtime(self):
        for raw in ('{"concurrency": 0}', '{"rpm": "120"}', '{"rpm": 1.5}', '{"tenant_concurrency": 9}',
                    '{"burst": 3}', '[]', '{}', 'not json'):
            with self.assertRaises((AssertionError, ValueError), msg=raw):
                step.ocr_budget({"PEARNLY_OCR_BUDGET": raw})


class DocumentAiRegions(unittest.TestCase):
    def test_without_runtime_value_writes_nothing(self):
        self.assertIsNone(step.document_ai_regions({}))

    def test_runtime_value_is_passed_through_in_priority_order(self):
        self.assertEqual(step.document_ai_regions({"PEARNLY_DOCUMENT_AI_REGIONS": json.dumps(REGIONS)}), REGIONS)

    def test_rejects_incomplete_or_repeated_regions(self):
        bad = [[], {"location": "us"}, [dict(REGIONS[0], version="")], [dict(REGIONS[0], rpm=0)],
               [REGIONS[0], dict(REGIONS[0])]]
        for value in bad:
            with self.assertRaises(AssertionError, msg=value):
                step.document_ai_regions({"PEARNLY_DOCUMENT_AI_REGIONS": json.dumps(value)})


if __name__ == "__main__":
    unittest.main()
