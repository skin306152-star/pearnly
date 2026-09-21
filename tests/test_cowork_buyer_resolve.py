"""Offline regressions for the migrated buyer-to-client decision core (batch 43).

Runs in the lab bench (the masters package needs the app). No database, no
Frappe: the data access is injected, which is exactly how the port is shaped.
"""

import unittest

from pearnly_cowork.masters import buyer_resolve


class CandidateConflict(unittest.TestCase):
    def test_one_candidate_is_never_a_conflict(self):
        self.assertFalse(buyer_resolve.buyer_candidates_conflict(None))
        self.assertFalse(buyer_resolve.buyer_candidates_conflict([]))
        self.assertFalse(buyer_resolve.buyer_candidates_conflict(["ACME Co., Ltd."]))
        self.assertFalse(buyer_resolve.buyer_candidates_conflict(["", "   "]))

    def test_an_abbreviation_of_the_same_name_is_not_a_conflict(self):
        self.assertFalse(buyer_resolve.buyer_candidates_conflict(
            ["ACME Co., Ltd.", "acme co., ltd."]))
        self.assertFalse(buyer_resolve.buyer_candidates_conflict(["ACME", "ACME Co., Ltd."]))
        self.assertFalse(buyer_resolve.buyer_candidates_conflict(["", "ACME"]))
        self.assertFalse(buyer_resolve.buyer_candidates_conflict(
            ["ACME Co., Ltd.", "ACME Co., Ltd.", "ACME"]))

    def test_two_unrelated_buyers_are_a_conflict(self):
        self.assertTrue(buyer_resolve.buyer_candidates_conflict(["ACME Co., Ltd.", "BBB Shop"]))
        self.assertTrue(buyer_resolve.buyer_candidates_conflict(
            ["ACME Co., Ltd.", "ACME Co., Ltd.", "BBB Shop"]))


class TaxNormalisation(unittest.TestCase):
    def test_dashes_and_spaces_come_out(self):
        self.assertEqual(buyer_resolve.normalize_tax("0-1055-48103-91-2"), "0105548103912")
        self.assertEqual(buyer_resolve.normalize_tax(" 0105548103912 "), "0105548103912")
        self.assertEqual(buyer_resolve.normalize_tax(None), "")
        self.assertEqual(buyer_resolve.normalize_tax(""), "")

    def test_only_thirteen_digits_are_a_valid_tax_id(self):
        self.assertTrue(buyer_resolve.is_valid_tax_id("0105548103912"))
        self.assertFalse(buyer_resolve.is_valid_tax_id("010554810391"))
        self.assertFalse(buyer_resolve.is_valid_tax_id("01055481039123"))
        self.assertFalse(buyer_resolve.is_valid_tax_id("01055481039AB"))


CLIENTS = [
    {"client_id": 1, "name": "ACME Co., Ltd.", "short_name": "ACME",
     "tax_id": "0-1055-48103-91-2"},
    {"client_id": 2, "name": "Bangkok Logistics Co., Ltd.", "short_name": "BKK Log",
     "tax_id": "0105559999999"},
    {"client_id": 3, "name": "Siam Foods", "short_name": "", "tax_id": None},
]


class MatchClient(unittest.TestCase):
    def test_no_buyer_name_matches_nothing(self):
        self.assertIsNone(buyer_resolve.match_client(None, None, clients=CLIENTS))
        self.assertIsNone(buyer_resolve.match_client("   ", None, clients=CLIENTS))

    def test_the_learned_memory_wins(self):
        match = buyer_resolve.match_client("anything at all", None, memory={
            "client_id": 7, "client_name": "Remembered Co."}, clients=CLIENTS)
        self.assertEqual(match, {"client_id": 7, "client_name": "Remembered Co.",
                                "confidence": 1.0, "match_source": "memory"})

    def test_a_tax_id_matches_however_it_is_punctuated(self):
        match = buyer_resolve.match_client("Some Other Name", "0105548103912", clients=CLIENTS)
        self.assertEqual((match["client_id"], match["confidence"], match["match_source"]),
                         (1, 0.98, "tax_id_exact"))

    def test_a_short_tax_number_is_not_compared(self):
        # Fewer than ten digits after normalisation: the tax layer is skipped and
        # the name layers decide.
        match = buyer_resolve.match_client("Some Other Name", "0105548", clients=CLIENTS)
        self.assertIsNone(match)

    def test_a_name_that_matches_exactly(self):
        match = buyer_resolve.match_client("acme co., ltd.", None, clients=CLIENTS)
        self.assertEqual((match["client_id"], match["confidence"], match["match_source"]),
                         (1, 0.95, "name_exact"))

    def test_a_short_name_that_matches_exactly(self):
        match = buyer_resolve.match_client("bkk log", None, clients=CLIENTS)
        self.assertEqual((match["client_id"], match["confidence"], match["match_source"]),
                         (2, 0.90, "short_name_exact"))

    def test_the_exact_layers_return_before_any_approximation(self):
        # "Siam Foods" is an exact name even though "ACME" would also be a substring
        # of nothing here; order is what matters.
        match = buyer_resolve.match_client("siam foods", None, clients=CLIENTS)
        self.assertEqual((match["client_id"], match["match_source"]), (3, "name_exact"))

    def test_a_substring_keeps_the_ratio_as_confidence(self):
        buyer = "ACME Co., Ltd. Bangkok Branch"
        match = buyer_resolve.match_client(buyer, None, clients=CLIENTS)
        self.assertEqual(match["match_source"], "name_substring")
        stored = "ACME Co., Ltd."
        ratio = min(len(stored), len(buyer)) / max(len(stored), len(buyer))
        self.assertAlmostEqual(match["confidence"], round(0.80 + ratio * 0.10, 3))
        self.assertEqual(match["client_id"], 1)
        # The same rule maps a shorter overlap to a lower confidence: an
        # approximation never reaches the auto-assign band on its own.
        self.assertLess(match["confidence"], buyer_resolve.AUTO_ASSIGN_CONFIDENCE)

    def test_the_best_approximation_wins(self):
        match = buyer_resolve.match_client("BKK Log", None, clients=CLIENTS)
        self.assertEqual(match["match_source"], "short_name_exact")
        match = buyer_resolve.match_client("BKK Logistics", None, clients=CLIENTS)
        self.assertIn(match["match_source"], ("name_substring", "short_substring"))
        self.assertEqual(match["client_id"], 2)

    def test_nothing_similar_returns_nothing(self):
        self.assertIsNone(buyer_resolve.match_client("Completely Different", None, clients=CLIENTS))
        self.assertIsNone(buyer_resolve.match_client("Completely Different", None, clients=[]))


class ResolveDecision(unittest.TestCase):
    def setUp(self):
        self.learned = []
        self.created = []
        self.learn = self.learned.append

        def create_by_tax_id(tax_clean, buyer_name):
            self.created.append((tax_clean, buyer_name))
            return 42

        self.create_by_tax_id = create_by_tax_id

    def resolve(self, name, tax=None, **overrides):
        arguments = {"clients": CLIENTS, "learn": self.learn,
                     "create_by_tax_id": self.create_by_tax_id}
        arguments.update(overrides)
        return buyer_resolve.resolve_buyer_client(name, tax, **arguments)

    def test_no_buyer_name_does_nothing(self):
        decision = self.resolve(None)
        self.assertEqual((decision["action"], decision["reason"]), ("none", "no_buyer_name"))
        self.assertEqual(self.learned, [])

    def test_conflicting_candidates_go_to_review_without_creating_anything(self):
        decision = self.resolve("ACME Co., Ltd.", "0105548103912",
                                buyer_candidates=["ACME Co., Ltd.", "BBB Shop"])
        self.assertEqual((decision["action"], decision["reason"]),
                         ("review", "buyer_candidates_conflict"))
        self.assertEqual((self.learned, self.created), ([], []))

    def test_an_exact_tax_match_is_assigned_and_learned(self):
        decision = self.resolve("Some Other Name", "0-1055-48103-91-2")
        self.assertEqual((decision["action"], decision["client_id"], decision["reason"]),
                         ("assigned", 1, "existing_high_confidence"))
        self.assertEqual((decision["confidence"], decision["match_source"]), (0.98, "tax_id_exact"))
        self.assertEqual(self.learned, [1])

    def test_a_memory_hit_is_assigned(self):
        decision = self.resolve("Anything", None, memory={"client_id": 7,
                                                          "client_name": "Remembered Co."})
        self.assertEqual((decision["action"], decision["client_id"], decision["confidence"]),
                         ("assigned", 7, 1.0))
        self.assertEqual(self.learned, [7])

    def test_a_new_valid_tax_is_created_and_learned(self):
        decision = self.resolve("Brand New Buyer Co., Ltd.", "0105551234567")
        self.assertEqual((decision["action"], decision["client_id"], decision["confidence"],
                          decision["match_source"], decision["reason"]),
                         ("created", 42, 0.98, "tax_id_create", "auto_created_by_tax_id"))
        self.assertEqual(self.created, [("0105551234567", "Brand New Buyer Co., Ltd.")])
        self.assertEqual(self.learned, [42])

    def test_a_creation_that_fails_goes_to_review(self):
        decision = self.resolve("Brand New Buyer Co., Ltd.", "0105551234567",
                                create_by_tax_id=lambda tax, name: None)
        self.assertEqual((decision["action"], decision["reason"]),
                         ("review", "create_by_tax_failed"))
        self.assertEqual(self.learned, [])

    def test_a_near_name_without_a_tax_is_only_suggested(self):
        decision = self.resolve("ACME Co., Ltd. Bangkok Branch")
        self.assertEqual((decision["action"], decision["client_id"], decision["reason"]),
                         ("suggest", 1, "name_suggestion_no_tax"))
        # A suggestion is never bound and never learned.
        self.assertEqual((self.learned, self.created), ([], []))

    def test_no_tax_and_no_match_goes_to_review(self):
        decision = self.resolve("Completely Different Trading")
        self.assertEqual((decision["action"], decision["reason"]), ("review", "no_tax_no_match"))
        self.assertEqual(self.created, [])

    def test_a_short_tax_is_not_valid_so_no_client_is_created(self):
        decision = self.resolve("Completely Different Trading", "0105548")
        self.assertEqual((decision["action"], decision["reason"]), ("review", "no_tax_no_match"))
        self.assertEqual(self.created, [])

    def test_the_suggestion_band_does_not_reach_the_auto_assign_band(self):
        # An exactly matching short name is only 0.90, so it is a suggestion: the
        # thresholds are the guard against merging two legal entities that merely
        # look alike.
        decision = self.resolve("BKK Log")
        self.assertEqual((decision["action"], decision["confidence"],
                          decision["match_source"]),
                         ("suggest", 0.90, "short_name_exact"))
        self.assertEqual(self.learned, [])

    def test_a_bypassed_learner_is_allowed(self):
        decision = buyer_resolve.resolve_buyer_client("Some Other Name", "0105548103912",
                                                      clients=CLIENTS)
        self.assertEqual(decision["action"], "assigned")
        self.assertEqual(decision["client_id"], 1)


if __name__ == "__main__":
    unittest.main()
