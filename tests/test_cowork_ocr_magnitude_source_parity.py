"""Differential check of the migrated supplier magnitude baseline.

CLI: --source <fixed-source-root>, or --source-stdin with the fixed source text as
JSON (used to pipe the source into the bench that has pydantic). The pinned module
only imports the standard library, so it runs from its fixed source text with no
legacy application, database, file, network, model or export code. Both sides answer
the same questions and the answers are compared verbatim.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import random
import sys

from pearnly_cowork.ocr.validation import magnitude

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/ocr/magnitude_baseline.py":
        "94c176047cd5658b87cc4181a173efb4520206233e52728b13739142b3b471ff",
}


def load_legacy(root=None, source_text=None):
	path = "services/ocr/magnitude_baseline.py"
	data = (source_text[path].encode("utf-8") if source_text is not None
		else (Path(root) / path).read_bytes())
	assert hashlib.sha256(data).hexdigest() == HASHES[path], f"Source hash mismatch: {path}"
	namespace = {"__name__": "fixed_magnitude_baseline"}
	exec(compile(ast.parse(data.decode("utf-8")), path, "exec"), namespace)
	return namespace


def outcome(callable_, *arguments, **keywords):
	try:
		return "ok", callable_(*arguments, **keywords)
	except Exception as error:
		return "error", type(error).__name__, str(error)


def cases(rng):
	totals = [None, 0, -5, 0.01, 1, 44.67, 1780.0, 1795.0, 99999.0, "1780", True, [], {}]
	histories = [
		[],
		None,
		[1780.0],
		[1780.0] * 4,
		[1780.0] * 5,
		[1780.0, 1810.0, 1795.0, 1800.0, 1790.0],
		[0, -1, "x", None, 5.0, 10.0, 20.0, 40.0],
		[1780.0, True, "x", 1790.0, 1795.0, 1800.0, 1810.0],
		[1e9, 2e9, 3e9, 4e9, 5e9],
	]
	for total in totals:
		for history in histories:
			yield {"new_total": total, "history_totals": history, "min_history": 5, "ratio": 20.0}
	for _ in range(400):
		history = [rng.choice([None, "x", 0, -3, 1.0, 44.67, 1780.0, 1800.0, 1e6, True])
			for _ in range(rng.randrange(0, 12))]
		yield {"new_total": rng.choice(totals[:10]), "history_totals": history,
			"min_history": rng.choice([1, 2, 5, 5, 9]), "ratio": rng.choice([2.0, 20.0, 100.0])}
	# Fixed shapes: a low misread, a high misread, the exact boundary and a withdrawal.
	yield {"new_total": 44.67, "history_totals": [1780.0, 1810.0, 1795.0, 1800.0, 1790.0],
		"min_history": 5, "ratio": 20.0}
	yield {"new_total": 40000.0, "history_totals": [1780.0, 1810.0, 1795.0, 1800.0, 1790.0],
		"min_history": 5, "ratio": 20.0}
	yield {"new_total": 35800.0, "history_totals": [1780.0, 1810.0, 1795.0, 1800.0, 1790.0],
		"min_history": 5, "ratio": 20.0}
	yield {"new_total": 35801.0, "history_totals": [1780.0, 1810.0, 1795.0, 1800.0, 1790.0],
		"min_history": 5, "ratio": 20.0}


def run(root=None, source_text=None, rounds=500):
	legacy = load_legacy(root, source_text)
	rng = random.Random(20260921)
	counts = {"batches": 0, "flagged_low": 0, "flagged_high": 0, "withdrawn": 0}
	for case in cases(rng):
		expected = outcome(legacy["magnitude_anomaly_reason"], case["new_total"],
			case["history_totals"], min_history=case["min_history"], ratio=case["ratio"])
		produced = outcome(magnitude.magnitude_anomaly_reason, case["new_total"],
			case["history_totals"], min_history=case["min_history"], ratio=case["ratio"])
		assert expected == produced, (case, expected, produced)
		counts["batches"] += 1
		reason = produced[1] if produced[0] == "ok" else None
		if reason:
			counts["flagged_low"] += 1 if "低" in reason else 0
			counts["flagged_high"] += 1 if "高" in reason else 0
		else:
			counts["withdrawn"] += 1
	print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
	if (counts["batches"] < rounds or counts["flagged_low"] < 20
			or counts["flagged_high"] < 20 or counts["withdrawn"] < 100):
		raise SystemExit("fixture coverage below the floor")
	raise SystemExit(0)


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument("--source", type=Path)
	group.add_argument("--source-stdin", action="store_true")
	arguments = parser.parse_args()
	if arguments.source_stdin:
		run(None, json.load(sys.stdin))
	else:
		run(arguments.source)
