"""Fixed-source S8 predicate and cents-domain balance-chain differential check."""
import argparse
import ast
import hashlib
import itertools
import json
from pathlib import Path
import random

from pearnly_cowork.reconciliation import bank_review as review

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
SOURCE_PATH = "services/importer/stmt_review.py"
SOURCE_HASH = "cd28ed22e9d121a1870c070ebd63e60f99f9f24c067c07f4fc31fc75969ec589"


def compare(root):
	data = (Path(root) / SOURCE_PATH).read_bytes()
	assert hashlib.sha256(data).hexdigest() == SOURCE_HASH
	tree = ast.parse(data)
	functions = {"needs_review", "_f", "recompute_balance_chain"}
	selected = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
		ast.alias(name="annotations")], level=0)] + [node for node in tree.body
		if isinstance(node, ast.FunctionDef) and node.name in functions], type_ignores=[])
	namespace = {}
	exec(compile(ast.fix_missing_locations(selected), SOURCE_PATH, "exec"), namespace)
	gate_cases = 0
	for ok, rows, low, repaired, complete in itertools.product(
			(False, True), ([], [1]), (0, 1, 4), (0, 1, 4), (None, False, True)):
		value = dict(ok=ok, rows=rows, low_conf_count=low, amount_fixed_count=repaired,
			completeness={"ok": complete})
		assert review.needs_review(value) == namespace["needs_review"](value)
		gate_cases += 1
	rng = random.Random(53)
	chain_cases = 0
	for _ in range(1000):
		opening = rng.randrange(-100000, 100000)
		previous, rows = opening, []
		for index in range(rng.randrange(1, 12)):
			deposit, withdrawal = rng.randrange(10000), rng.randrange(10000)
			balance = previous + deposit - withdrawal + rng.choice((0, 0, 2, -2, 3, -3))
			rows.append({"idx": None, "date": None, "description": "row",
				"deposit": f"{deposit / 100:.2f}", "withdrawal": f"{withdrawal / 100:.2f}",
				"balance": f"{balance / 100:.2f}"})
			previous = balance
		validated, _ = review.validate_rows(rows, [], [])
		actual = review.balance_chain(validated, str(opening / 100))
		legacy = namespace["recompute_balance_chain"](
			[{**row, "idx": index} for index, row in enumerate(rows)], opening / 100)
		for left, right in zip(actual, legacy):
			assert left["idx"] == right["idx"] and left["ok"] == right["ok"]
			assert all(float(left[key]) == right[key] for key in ("expected", "actual", "diff"))
		chain_cases += 1
	return {"source_sha": SOURCE_SHA, "source_hash": SOURCE_HASH,
		"predicate_cases": gate_cases, "balance_chains": chain_cases, "result": "PASS"}


if __name__ == "__main__":
	parser = argparse.ArgumentParser()
	parser.add_argument("--source", required=True)
	print(json.dumps(compare(parser.parse_args().source), sort_keys=True))
