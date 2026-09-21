"""Offline regressions for the migrated supplier magnitude baseline."""

import unittest

from pearnly_cowork.ocr.validation import magnitude


class MagnitudeBaseline(unittest.TestCase):
	def test_a_scale_sized_gap_is_flagged_in_both_directions(self):
		history = [1780.0, 1810.0, 1795.0, 1800.0, 1790.0]
		low = magnitude.magnitude_anomaly_reason(44.67, history)
		high = magnitude.magnitude_anomaly_reason(40000.0, history)
		self.assertIn("低", low)
		self.assertIn("44.67", low)
		self.assertIn("1795.00", low)
		self.assertIn("高", high)
		self.assertIn("40000", high)

	def test_an_ordinary_swing_is_not_flagged(self):
		history = [1780.0, 1810.0, 1795.0, 1800.0, 1790.0]
		self.assertIsNone(magnitude.magnitude_anomaly_reason(1795.0, history))
		# Twenty times the median is the boundary itself, so it is not over it.
		self.assertIsNone(magnitude.magnitude_anomaly_reason(1795.0 * 20, history))
		self.assertIsNotNone(magnitude.magnitude_anomaly_reason(1795.0 * 20 + 1, history))

	def test_too_little_history_means_no_judgement(self):
		self.assertIsNone(magnitude.magnitude_anomaly_reason(44.67, [1780.0, 1810.0, 1795.0,
			1800.0]))
		self.assertIsNone(magnitude.magnitude_anomaly_reason(44.67, []))
		self.assertIsNone(magnitude.magnitude_anomaly_reason(44.67, None))
		# The gate is configurable for a caller that knows better.
		self.assertIsNotNone(magnitude.magnitude_anomaly_reason(44.67, [1780.0], min_history=1))

	def test_a_missing_or_non_positive_total_is_left_to_the_other_gates(self):
		history = [1780.0, 1810.0, 1795.0, 1800.0, 1790.0]
		for total in (None, 0, -100, "1780"):
			with self.subTest(total=total):
				self.assertIsNone(magnitude.magnitude_anomaly_reason(total, history))

	def test_a_boolean_counts_as_a_number_exactly_as_the_deployed_rule_does(self):
		history = [1780.0, 1810.0, 1795.0, 1800.0, 1790.0]
		# ``True`` is a positive number to the deployed type test, so it is read as 1
		# and flagged like any other low total; the quirk is kept, not repaired.
		self.assertIsNotNone(magnitude.magnitude_anomaly_reason(True, history))
		self.assertIsNotNone(magnitude.magnitude_anomaly_reason(44.67, history + [True]))

	def test_dirty_history_rows_do_not_count_towards_the_baseline(self):
		history = [1780.0, "x", None, 0, -5, 1790.0, 1795.0, 1800.0, 1810.0]
		# Five valid rows of nine: the baseline stands and the low read is flagged.
		self.assertIsNotNone(magnitude.magnitude_anomaly_reason(44.67, history))
		# One valid row fewer and the rule withdraws.
		self.assertIsNone(magnitude.magnitude_anomaly_reason(44.67, history[:-1]))

	def test_the_median_keeps_a_dirty_outlier_from_moving_the_baseline(self):
		history = [1780.0, 1790.0, 1795.0, 1800.0, 1810.0, 999999.0]
		self.assertIsNone(magnitude.magnitude_anomaly_reason(1795.0, history))
		self.assertIsNotNone(magnitude.magnitude_anomaly_reason(999999.0 * 25, history))

	def test_the_thresholds_are_exposed_for_a_caller(self):
		self.assertEqual(magnitude.RATIO, 20.0)
		self.assertEqual(magnitude.MIN_HISTORY, 5)


if __name__ == "__main__":
	unittest.main()
