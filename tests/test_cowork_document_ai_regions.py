"""Document AI 三区域切换（用户 2026-09-30：「遇到限流马上转区域，不要原地重试，三个区域灵活调用」）。离线，不连站点。"""
import json
import unittest

from pearnly_core.execution.errors import Deferred
from pearnly_cowork.ocr.enterprise import pages
from pearnly_cowork.ocr.providers.client import ProviderClient


def body(count=1):
	return {"document": {"text": "x", "pages": [{"pageNumber": number, "layout": {"textAnchor": {}}}
		for number in range(1, count + 1)]}}


class Response:
	status_code = 200

	def __init__(self, payload):
		self.payload = payload

	def json(self):
		return self.payload


class Regions(unittest.TestCase):
	def client(self, answers, *, saved=None, regions=None, in_queue=False):
		"""``answers[location]``：该区域依次的结果（Deferred 实例 = 被限流，其余 = 正常返回）。"""
		self.calls, self.slept, self.routing = [], [], []
		saved = saved or {}

		def post(**values):
			location = json.loads(values["model"])[3]
			self.calls.append(location)
			self.routing.append({key: values.get(key) for key in ("rpm", "wait", "label")})
			answer = answers[location].pop(0)
			if isinstance(answer, Exception):
				raise answer
			return Response(answer)

		return ProviderClient({}, project="p", post=post, checkpoint_key=lambda payload, identity: identity,
			reject_checkpoint=lambda *_a: None, terminal_read=lambda _k: None, terminal_write=lambda *_a: None,
			feedback=lambda *_a: None, scope=lambda *args: "|".join(map(str, args)), access_token=lambda: "FAKE",
			require_context=lambda: None, checkpoint_read=lambda key: saved.get(key), regions=regions,
			sleep=self.slept.append, in_queue_job=lambda: in_queue)

	def read(self, client):
		return client.document_ai_call(b"img", "image/jpeg", expected_pages=1, stage="enterprise", page_number=1)

	def test_a_rate_limited_region_hands_over_to_the_next_at_once(self):
		client = self.client({"asia-southeast1": [Deferred("provider_429", 12)], "asia-south1": [body()], "us": []})
		self.read(client)
		self.assertEqual(self.calls, ["asia-southeast1", "asia-south1"])
		self.assertEqual(self.slept, [])  # 没有原地等
		self.assertEqual(self.routing[0], {"rpm": 10, "wait": False, "label": "documentai:asia-southeast1"})

	def test_a_region_out_of_its_minute_quota_also_hands_over(self):
		client = self.client({"asia-southeast1": [Deferred("capacity", 6)], "asia-south1": [Deferred("provider_429", 5)],
			"us": [body()]})
		self.read(client)
		self.assertEqual(self.calls, ["asia-southeast1", "asia-south1", "us"])
		self.assertEqual(self.routing[2]["rpm"], 120)

	def test_all_regions_limited_wait_briefly_in_a_queue_job_then_start_over(self):
		client = self.client({"asia-southeast1": [Deferred("provider_429", 8), body()],
			"asia-south1": [Deferred("capacity", 3)], "us": [Deferred("provider_429", 5)]}, in_queue=True)
		self.read(client)
		self.assertEqual(self.slept, [3.0])
		self.assertEqual(self.calls, ["asia-southeast1", "asia-south1", "us", "asia-southeast1"])

	def test_all_regions_limited_outside_a_queue_job_defers_with_the_shortest_wait(self):
		client = self.client({"asia-southeast1": [Deferred("provider_429", 8)], "asia-south1": [Deferred("capacity", 3)],
			"us": [Deferred("provider_429", 5)]})
		with self.assertRaises(Deferred) as caught:
			self.read(client)
		self.assertEqual(caught.exception.delay, 3.0)

	def test_other_deferrals_do_not_switch_region(self):
		client = self.client({"asia-southeast1": [Deferred("capacity_unavailable", 15)], "asia-south1": [], "us": []})
		with self.assertRaises(Deferred):
			self.read(client)
		self.assertEqual(self.calls, ["asia-southeast1"])

	def test_a_chunk_already_read_in_another_region_is_taken_back_not_paid_again(self):
		version = pages.REGIONS[1]["version"]
		identity = json.dumps(["cowork", "documentai", "p", "asia-south1", version, "enterprise", 1, 0])
		client = self.client({"asia-southeast1": [], "asia-south1": [body()], "us": []},
			saved={identity: {"state": "done"}})
		self.read(client)
		self.assertEqual(self.calls, ["asia-south1"])

	def test_each_region_uses_its_own_processor_and_version(self):
		urls = []
		client = self.client({"asia-southeast1": [Deferred("provider_429", 5)], "asia-south1": [Deferred("provider_429", 5)],
			"us": [body()]})
		original = client._post
		client._post = lambda **values: (urls.append(values["url"]), original(**values))[1]
		self.read(client)
		self.assertTrue(urls[2].startswith("https://us-documentai.googleapis.com/v1/projects/p/locations/us/processors/"
			"2a5bdf2779fcc187/processorVersions/pretrained-ocr-v2.1-2024-08-07"))
		self.assertIn("asia-south1-documentai.googleapis.com", urls[1])


class RegionConfig(unittest.TestCase):
	def test_site_list_replaces_the_default_and_a_bad_list_falls_back(self):
		one = [{"location": "asia-southeast1", "processor": "abc123", "version": "pretrained-ocr-v2.1.1-2025-01-31", "rpm": 60}]
		self.assertEqual(pages.regions(one), tuple(one))
		for bad in ([{"location": "asia-southeast1"} | {"processor": ""}], [{"location": "x", "processor": "y", "rpm": 0}],
				[one[0], one[0]], ["asia-southeast1"], [{"location": "A B", "processor": "y"}]):
			self.assertEqual(pages.regions(bad), pages.REGIONS, bad)
		self.assertEqual(pages.regions(None), pages.REGIONS)

	def test_the_old_single_region_environment_override_still_works(self):
		chosen = pages.regions(None, {"ENTERPRISE_OCR_LOCATION": "us", "ENTERPRISE_OCR_PROCESSOR_ID": "zzz"})
		self.assertEqual([(item["location"], item["processor"]) for item in chosen], [("us", "zzz")])


if __name__ == "__main__":
	unittest.main()
