"""部署 / 重启 / OOM 中途被杀的对账续跑（2026-09-30 测试机重载打断压测 rI）。离线：队列、缓存、数据库用替身。"""
import json
import unittest
from unittest import mock

from pearnly_cowork.recon_center import jobs


class FakeJob:
	def __init__(self, exc="Moved to FailedJobRegistry, due to AbandonedJobError, at 2026-09-30", status="failed",
			options=None, user="u@x"):
		self.exc_info, self.status, self.deleted = exc, status, False
		self.kwargs = {"user": user, "kwargs": {"recon_job": "J1", "kind": "bank", "workspace": "ws", "title": "B01",
			"files": {"left": ["F1"], "right": ["F2"]}, "options": json.dumps(options or {"lang": "zh"})}}

	def get_status(self, refresh=False):
		return self.status

	def delete(self):
		self.deleted = True


class Resume(unittest.TestCase):
	def run_resume(self, job, *, files_exist=True, locked=False, finished=False, owner=None, newer=False):
		conn = mock.Mock()
		conn.set.return_value = not locked
		state = {}
		with mock.patch("frappe.utils.background_jobs.get_job", return_value=job), \
				mock.patch("frappe.utils.background_jobs.get_redis_conn", return_value=conn), \
				mock.patch.object(jobs.frappe, "set_user"), \
				mock.patch.object(jobs.frappe, "session", mock.Mock(user="Administrator")), \
				mock.patch.object(jobs.frappe, "local", mock.Mock(site="s")), \
				mock.patch.object(jobs.frappe, "db", mock.Mock(exists=lambda doctype, name: files_exist)), \
				mock.patch.object(jobs, "_recovered", return_value={"state": "done"} if finished else None), \
				mock.patch.object(jobs, "_superseded", return_value=newer), \
				mock.patch.object(jobs, "_state", side_effect=lambda job_id, **values: state.update(values)), \
				mock.patch.object(jobs.progress, "active_remove"), \
				mock.patch.object(jobs, "_", side_effect=lambda text: text), \
				mock.patch.object(jobs, "enqueue") as enqueue:
			outcome = jobs.resume_if_abandoned("J1", owner=owner)
		return outcome, enqueue, state

	def test_a_killed_reconciliation_is_queued_again_with_its_own_files(self):
		job = FakeJob()
		outcome, enqueue, _state = self.run_resume(job)
		self.assertEqual(outcome, jobs.RESUMED)
		self.assertTrue(job.deleted)
		self.assertEqual(enqueue.call_args.args[:5], ("J1", "bank", "ws", "B01", {"left": ["F1"], "right": ["F2"]}))
		self.assertEqual(enqueue.call_args.args[5], {"lang": "zh", "resumed": 1})

	def test_a_worker_killed_by_the_system_is_resumed_too(self):
		outcome, _enqueue, _state = self.run_resume(FakeJob(exc="Work-horse terminated unexpectedly; waitpid returned 9"))
		self.assertEqual(outcome, jobs.RESUMED)

	def test_a_real_failure_or_a_timeout_is_not_resumed(self):
		for exc in ("Traceback ... ValidationError", "JobTimeoutException: Task exceeded maximum timeout value"):
			outcome, enqueue, _state = self.run_resume(FakeJob(exc=exc))
			self.assertIsNone(outcome)
			enqueue.assert_not_called()

	def test_a_second_kill_or_missing_files_says_interrupted(self):
		for job, present in ((FakeJob(options={"resumed": 1}), True), (FakeJob(), False)):
			outcome, enqueue, state = self.run_resume(job, files_exist=present)
			self.assertEqual(outcome, jobs.INTERRUPTED)
			enqueue.assert_not_called()
			self.assertEqual((state["state"], state["error"]), ("failed", "reconciliation.interrupted"))

	def test_only_one_caller_resumes_and_a_saved_result_is_left_alone(self):
		self.assertIsNone(self.run_resume(FakeJob(), locked=True)[0])
		self.assertIsNone(self.run_resume(FakeJob(), finished=True)[0])

	def test_the_page_only_resumes_its_own_reconciliation(self):
		self.assertIsNone(self.run_resume(FakeJob(), owner="other@x")[0])
		self.assertEqual(self.run_resume(FakeJob(), owner="u@x")[0], jobs.RESUMED)

	def test_a_later_submission_of_the_same_files_replaces_the_killed_one(self):
		outcome, enqueue, state = self.run_resume(FakeJob(), newer=True)
		self.assertEqual(outcome, jobs.SUPERSEDED)
		enqueue.assert_not_called()
		self.assertEqual((state["state"], state["error"]), ("failed", "reconciliation.superseded"))

	def test_a_running_job_is_not_touched(self):
		self.assertIsNone(self.run_resume(FakeJob(status="started"))[0])


class LatestSubmission(unittest.TestCase):
	"""同一个人、同一种对账、同样的源文件（按内容哈希，不看标题）只让最新一次提交出结果。"""

	def run_with(self, hashes):
		store = {}
		conn = mock.Mock()
		conn.set.side_effect = lambda key, value, ex=None: store.__setitem__(key, value)
		conn.get.side_effect = lambda key: store.get(key)
		values = {"L1": hashes[0], "R1": hashes[1], "L2": hashes[0], "R2": hashes[1]}
		return mock.patch("frappe.utils.background_jobs.get_redis_conn", return_value=conn), \
			mock.patch.object(jobs.frappe, "db", mock.Mock(get_value=lambda doctype, name, field: values.get(name))), \
			mock.patch.object(jobs.frappe, "local", mock.Mock(site="s")), \
			mock.patch.object(jobs.frappe, "session", mock.Mock(user="u@x"))

	def test_the_older_of_two_same_submissions_is_superseded(self):
		patches = self.run_with(("a", "b"))
		with patches[0], patches[1], patches[2], patches[3]:
			jobs._mark_latest("bank", {"left": ["L1"], "right": ["R1"]}, "J1")
			jobs._mark_latest("bank", {"left": ["L2"], "right": ["R2"]}, "J2")
			self.assertTrue(jobs._superseded("bank", {"left": ["L1"], "right": ["R1"]}, "J1"))
			self.assertFalse(jobs._superseded("bank", {"left": ["L2"], "right": ["R2"]}, "J2"))
			# 另一种对账、同样的文件不算
			self.assertFalse(jobs._superseded("income", {"left": ["L1"], "right": ["R1"]}, "J1"))


if __name__ == "__main__":
	unittest.main()
