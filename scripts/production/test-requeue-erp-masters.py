"""回退时把 erp_masters 里排着的任务挪回 long（`requeue-erp-masters.py`）：不连 Redis 的检查。"""
from pathlib import Path
import runpy
import unittest

script = runpy.run_path(str(Path(__file__).with_name("requeue-erp-masters.py")))


class Queue:
    def __init__(self, jobs=()):
        self.jobs = list(jobs)

    def remove(self, job):
        self.jobs.remove(job)

    def enqueue_job(self, job):
        self.jobs.append(job)


class Requeue(unittest.TestCase):
    def test_queued_jobs_move_to_long_in_order_and_keep_their_ids(self):
        queues = {"erp_masters": Queue(["erp-express-sync:t:CO-1", "erp-express-land:t:CO-2"]), "long": Queue(["other"])}
        self.assertEqual(script["move"](queues.__getitem__), 2)
        self.assertEqual(queues["erp_masters"].jobs, [])
        self.assertEqual(queues["long"].jobs, ["other", "erp-express-sync:t:CO-1", "erp-express-land:t:CO-2"])

    def test_running_again_moves_nothing(self):
        queues = {"erp_masters": Queue(), "long": Queue(["other"])}
        self.assertEqual(script["move"](queues.__getitem__), 0)
        self.assertEqual(queues["long"].jobs, ["other"])

    def test_names_match_the_queue_the_app_enqueues_on(self):
        source = (Path(__file__).parents[2] / "apps/pearnly_erp_portal/pearnly_erp_portal/express/masters.py").read_text()
        self.assertIn(f'QUEUE = "{script["SOURCE"]}"', source)
        runtime = runpy.run_path(str(Path(__file__).with_name("configure-erp-runtime.py")))
        self.assertIn(script["SOURCE"], runtime["configured"]({})["workers"])


if __name__ == "__main__":
    unittest.main()
