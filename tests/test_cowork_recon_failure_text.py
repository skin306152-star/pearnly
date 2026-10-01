"""对账失败给用户的提示：一时的系统问题、数据问题、程序错误各说各的。离线：不连站点。"""
import unittest
from unittest.mock import patch

import frappe

from pearnly_cowork.recon_center import jobs


class FailureText(unittest.TestCase):
	def text(self, error):
		with patch.object(jobs.frappe, "_", side_effect=lambda key: key):
			return jobs._failure_text(error, lambda error: f"data: {error}")

	def test_a_passing_database_problem_says_busy_and_try_later(self):
		for error in (frappe.QueryTimeoutError("lock"), frappe.QueryDeadlockError("deadlock"), ConnectionError("redis"),
				TimeoutError("slow")):
			self.assertEqual(self.text(error), "reconciliation.system_busy")

	def test_a_data_problem_says_what_is_wrong(self):
		self.assertEqual(self.text(frappe.ValidationError("bad file")), "data: bad file")
		self.assertEqual(self.text(ValueError("bad value")), "data: bad value")

	def test_a_program_error_is_not_called_busy(self):
		# 2026-10-02 多人压测 B08-01：TypeError 被说成「系统繁忙、稍后重试」，重试照样失败。
		for error in (TypeError("unsupported operand"), KeyError("rows"), AttributeError("balance")):
			self.assertEqual(self.text(error), "reconciliation.program_error")


if __name__ == "__main__":
	unittest.main()
