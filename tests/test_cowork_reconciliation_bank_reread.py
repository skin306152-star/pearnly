"""Offline regressions for the migrated re-read arbitration (batch 40).

The page reader is injected, so no PDF rendering and no model call happens here.
"""

import unittest

from pearnly_cowork.reconciliation import bank_reread, bank_statement


def rows(*entries, opening=0.0):
	"""Statement rows shaped like the balance chain leaves them."""
	built = [
		bank_statement.StatementRow(
			date=None,
			description=description,
			withdrawal=withdrawal,
			deposit=deposit,
			balance=balance,
		)
		for description, withdrawal, deposit, balance in entries
	]
	return bank_statement.finalize_rows(built, opening)


GOOD_PAGE = [
	("โอนเงิน", 0.0, 100.0, 1100.0),
]
BROKEN_HEAD = [
	("โอนเงิน", 0.0, 100.0, 1100.0),
	("ค่าอาหาร", 50.0, 0.0, 9999.0),
]


class ChainBreaks(unittest.TestCase):
	def test_only_rows_the_chain_refused_are_counted(self):
		self.assertEqual(bank_reread.count_chain_breaks(rows(*GOOD_PAGE, opening=1000.0)), 0)
		self.assertEqual(bank_reread.count_chain_breaks(rows(*BROKEN_HEAD, opening=1000.0)), 1)
		self.assertEqual(bank_reread.count_chain_breaks([]), 0)


class RereadArbitration(unittest.TestCase):
	def test_a_non_pdf_never_triggers_a_reread(self):
		calls = []
		kept, escalations = bank_reread.maybe_reread_chain_breaks(
			rows(*BROKEN_HEAD, opening=1000.0), 1000.0, extension="xlsx",
			page_count=3, read_page=lambda page: calls.append(page))
		self.assertEqual(calls, [])
		self.assertEqual(escalations, [])
		self.assertEqual(len(kept), 2)

	def test_no_breaks_means_no_rendering_and_no_cost(self):
		calls = []
		kept, escalations = bank_reread.maybe_reread_chain_breaks(
			rows(*GOOD_PAGE, opening=1000.0), 1000.0,
			page_count=3, read_page=lambda page: calls.append(page))
		self.assertEqual(calls, [])
		self.assertEqual(escalations, [])
		self.assertEqual(len(kept), 1)

	def test_an_implausible_page_count_is_skipped(self):
		calls = []
		for page_count in (0, -1, bank_reread.MAX_REREAD_PAGES + 1):
			kept, escalations = bank_reread.maybe_reread_chain_breaks(
				rows(*BROKEN_HEAD, opening=1000.0), 1000.0,
				page_count=page_count, read_page=lambda page: calls.append(page))
			self.assertEqual(escalations, [])
			self.assertEqual(len(kept), 2)
		self.assertEqual(calls, [])

	def test_a_reread_with_fewer_breaks_is_adopted(self):
		calls = []
		# The pages are read in order and their rows are joined into one chain, so
		# the second page continues the first one's balance.
		pages = {
			1: [("โอนเงิน", 0.0, 100.0, 1100.0)],
			2: [("โอนเงิน", 0.0, 100.0, 1200.0)],
		}

		def read_page(page_number):
			calls.append(page_number)
			return rows(*pages[page_number], opening=1000.0)

		kept, escalations = bank_reread.maybe_reread_chain_breaks(
			rows(*BROKEN_HEAD, opening=1000.0), 1000.0, page_count=2, read_page=read_page)
		self.assertEqual(calls, [1, 2])
		self.assertEqual(len(kept), 2)
		self.assertEqual([row.balance for row in kept], [1100.0, 1200.0])
		self.assertEqual(len(escalations), 1)
		escalation = escalations[0]
		self.assertEqual(escalation, {"pages": [1, 2], "eye_from": "vision",
			"eye_to": "direct", "breaks_before": 1, "breaks_after": 0, "kept": True})

	def test_reread_pages_that_do_not_join_up_are_not_adopted(self):
		# Two pages that each look fine on their own can still break the chain once
		# joined; the arbitration is on the joined chain, exactly like the deployed one.
		same_page = lambda page: rows(*GOOD_PAGE, opening=1000.0)
		original = rows(*BROKEN_HEAD, opening=1000.0)
		kept, escalations = bank_reread.maybe_reread_chain_breaks(
			original, 1000.0, page_count=2, read_page=same_page)
		self.assertEqual([row.row_hash for row in kept], [row.row_hash for row in original])
		self.assertEqual(escalations[0]["breaks_after"], 1)
		self.assertEqual(escalations[0]["kept"], False)

	def test_a_reread_that_is_not_better_is_thrown_away(self):
		original = rows(*BROKEN_HEAD, opening=1000.0)
		kept, escalations = bank_reread.maybe_reread_chain_breaks(
			original, 1000.0, page_count=1,
			read_page=lambda page: rows(*BROKEN_HEAD, opening=1000.0))
		self.assertEqual([row.row_hash for row in kept], [row.row_hash for row in original])
		self.assertEqual(escalations[0]["kept"], False)
		self.assertEqual(escalations[0]["breaks_before"], 1)

	def test_a_reread_that_changes_nothing_is_thrown_away(self):
		original = rows(*BROKEN_HEAD, opening=1000.0)
		kept, escalations = bank_reread.maybe_reread_chain_breaks(
			original, 1000.0, page_count=1, read_page=lambda page: [])
		self.assertEqual(len(kept), 2)
		self.assertEqual(escalations[0]["kept"], False)
		self.assertEqual(escalations[0]["breaks_after"], 0)

	def test_one_failing_page_discards_the_whole_round(self):
		original = rows(*BROKEN_HEAD, opening=1000.0)

		def read_page(page_number):
			if page_number == 2:
				raise RuntimeError("render failed")
			return rows(*GOOD_PAGE, opening=1000.0)

		kept, escalations = bank_reread.maybe_reread_chain_breaks(
			original, 1000.0, page_count=2, read_page=read_page)
		# A half reread loses rows, which is worse than the original read.
		self.assertEqual([row.row_hash for row in kept], [row.row_hash for row in original])
		self.assertEqual(escalations, [])

	def test_the_page_limit_is_the_caller_choice(self):
		calls = []
		kept, escalations = bank_reread.maybe_reread_chain_breaks(
			rows(*BROKEN_HEAD, opening=1000.0), 1000.0, page_count=5,
			read_page=lambda page: calls.append(page), max_pages=4)
		self.assertEqual((calls, escalations), ([], []))
		self.assertEqual(len(kept), 2)

	def test_only_the_given_page_range_is_read(self):
		calls = []
		bank_reread.maybe_reread_chain_breaks(
			rows(*BROKEN_HEAD, opening=1000.0), 1000.0, page_count=3,
			read_page=lambda page: (calls.append(page), [])[1])
		self.assertEqual(calls, [1, 2, 3])


if __name__ == "__main__":
	unittest.main()
