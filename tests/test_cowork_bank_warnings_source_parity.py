"""Differential check of the migrated bank-reconciliation warnings and anchors.

CLI: --source <fixed-source-root>, or --source-stdin with the fixed source text as
JSON (used to pipe the source into the bench that has openpyxl). The pinned module
imports nothing outside the standard library, so no legacy application, database,
file, network, model or export code runs. Both sides answer the same questions and
the answers are compared verbatim.
"""
import argparse
import ast
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace
from typing import Optional

from pearnly_cowork.reconciliation import bank_warnings

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/recon/bank_recon_warnings.py":
        "73a84874c21a351c32b7dc8fe5e125a3e3f25a0adc8c714e6f8df9c81888c3bf",
}
LANGS = ("th", "en", "zh", "ja", "bogus", "")


def load_legacy(root=None, source_text=None):
	path = "services/recon/bank_recon_warnings.py"
	data = (source_text[path].encode("utf-8") if source_text is not None
		else (Path(root) / path).read_bytes())
	assert hashlib.sha256(data).hexdigest() == HASHES[path], f"Source hash mismatch: {path}"
	namespace = {"__name__": "fixed_bank_recon_warnings", "Optional": Optional}
	exec(compile(ast.parse(data.decode("utf-8")), path, "exec"), namespace)
	return namespace


def labels_match(legacy):
	"""Every label table and helper answer must match the deployed wording."""
	assert legacy["_BRV2_WARN"] == bank_warnings._BRV2_WARN
	assert legacy["_COMP_LBL"] == bank_warnings._COMP_LBL
	assert legacy["_MISSPAGE_LBL"] == bank_warnings._MISSPAGE_LBL
	cases = 0
	keys = list(bank_warnings._BRV2_WARN) + ["missing_key", ""]
	for key in keys:
		for lang in LANGS:
			assert legacy["brv2_warn"](key, lang) == bank_warnings.brv2_warn(key, lang), (key, lang)
			cases += 1
	# Every placeholder the migrated callers use must render identically.
	formatted = [
		("period_mismatch", {"g": "2026-06-01~2026-06-30", "s": "2026-07-01~2026-07-31"}),
		("gl_too_few", {"n": 3, "m": 237}),
		("completeness", {"detail": "存款笔数(账单印5/识别3)"}),
		("multi_account", {"n": 2, "codes": "123-4、567-8"}),
	]
	for key, fmt in formatted:
		for lang in LANGS:
			assert (legacy["brv2_warn"](key, lang, **fmt)
				== bank_warnings.brv2_warn(key, lang, **fmt)), (key, lang, fmt)
			cases += 1
	return cases, keys


def issues():
	return [
		{"type": "credit_count_mismatch", "printed": 5, "count": 3},
		{"type": "credit_count_mismatch", "printed": 40, "count": 3},
		{"type": "debit_count_mismatch", "printed": 9, "count": 9},
		{"type": "debit_count_mismatch", "printed": 30, "count": 1},
		{"type": "credit_sum_mismatch", "printed": 12345.6, "sum": 12000.0},
		{"type": "debit_sum_mismatch", "printed": -500.0, "sum": 0.0},
		{"type": "closing_mismatch", "printed": 560.0, "calc": 510.0},
		{"type": "balance_break", "count": 4},
		{"type": "balance_break", "count": 0},
		{"type": "unknown_type"},
		{},
	]


def completeness_cases(legacy):
	pool = issues()
	cases = 0
	floors = {"balance_break": 0, "misspage": 0, "sums": 0}
	for cut in range(len(pool) + 1):
		for lang in LANGS:
			subset = pool[:cut]
			expected = legacy["completeness_details"](subset, lang)
			produced = bank_warnings.completeness_details(subset, lang)
			assert expected == produced, (cut, lang, expected, produced)
			cases += 1
			if cut > 7 and expected and any("⚠" in line and "row" in line or "行" in line
					for line in expected):
				floors["balance_break"] += 1
			for line in expected:
				floors["misspage"] += 1 if line.startswith(("(", "（")) else 0
	for value in (None, [], [{"type": "credit_count_mismatch", "printed": 2, "count": 2}]):
		assert (legacy["completeness_details"](value, "en")
			== bank_warnings.completeness_details(value, "en")), value
		cases += 1
	if floors["balance_break"] < 1 or floors["misspage"] < 3:
		raise AssertionError(("completeness branches barely exercised", floors))
	return cases, floors


def rows(specs):
	return [SimpleNamespace(date=spec) for spec in specs]


def expected_messages(statement, gl, matched, lang):
	"""The three documented rules, restated from the inputs rather than the output.

	Any comparison the deployed code would raise on (mixed date types) leaves the
	answer empty, because the pinned function swallows the failure.
	"""
	out = []
	try:
		s_dates = [row.date for row in statement if row.date]
		g_dates = [row.date for row in gl if row.date]
		if s_dates and g_dates:
			smin, smax = min(s_dates), max(s_dates)
			gmin, gmax = min(g_dates), max(g_dates)
			if smax < gmin or gmax < smin:
				out.append(("period", bank_warnings.brv2_warn("period_mismatch", lang,
					g=f"{gmin}~{gmax}", s=f"{smin}~{smax}")))
		if len(statement) >= 20 and len(gl) * 5 < len(statement):
			out.append(("too_few", bank_warnings.brv2_warn("gl_too_few", lang,
				n=len(gl), m=len(statement))))
		if matched == 0 and (len(statement) + len(gl)) >= 10:
			out.append(("no_match", bank_warnings.brv2_warn("no_match", lang)))
	except TypeError:
		return []
	return out


def mismatch_cases(legacy):
	rng = random.Random(20260921)
	base = date(2026, 6, 1)
	pool = [None, base, base + timedelta(days=5), base + timedelta(days=59),
		base - timedelta(days=30)]
	counts = {"batches": 0, "statement_rows": 0, "gl_rows": 0, "period": 0, "too_few": 0,
		"no_match": 0}
	for _ in range(400):
		# A quarter of the batches draw the two sides from separate windows, so the
		# period rule is exercised by more than the fixed shapes below.
		if rng.random() < 0.25:
			statement_pool = [None, base, base + timedelta(days=5)]
			gl_pool = [None, base + timedelta(days=90), base + timedelta(days=120)]
		else:
			statement_pool = gl_pool = pool
		statement = rows([rng.choice(statement_pool) for _ in range(rng.randrange(0, 30))])
		gl = rows([rng.choice(gl_pool) for _ in range(rng.randrange(0, 30))])
		matched = rng.choice([0, 0, 1, 5])
		lang = rng.choice(LANGS)
		expected = legacy["detect_recon_mismatch"](statement, gl, matched, lang)
		produced = bank_warnings.detect_recon_mismatch(statement, gl, matched, lang)
		assert expected == produced, (statement, gl, matched, lang, expected, produced)
		restated = expected_messages(statement, gl, matched, lang)
		assert produced == [message for _, message in restated], (
			statement, gl, matched, lang, produced, restated)
		counts["batches"] += 1
		counts["statement_rows"] += len(statement)
		counts["gl_rows"] += len(gl)
		for category, _ in restated:
			counts[category] += 1
	# Fixed shapes: a period gap, a scale gap, no match, a clean pair and a side that raises.
	fixed = [
		([base] * 3, [base + timedelta(days=59)] * 3, 1, "th", 1, 0, 0),
		([base + timedelta(days=59)] * 25, [base] * 3, 2, "en", 1, 1, 0),
		([base] * 20, [base] * 20, 0, "zh", 0, 0, 1),
		([base] * 5, [base] * 5, 0, "ja", 0, 0, 1),
		([base] * 19, [base] * 3, 0, "th", 0, 0, 1),
		# The scale rule is strict: four ledger rows against twenty is exactly the
		# fifth, so it does not trip.
		([base] * 20, [base] * 4, 0, "th", 0, 0, 1),
		([], [], 0, "th", 0, 0, 0),
		([None, None], [None], 0, "th", 0, 0, 0),
		# Printed dates compare as text, which the deployed rule relies on.
		(["2026-06-01"], ["2026-07-01"], 0, "th", 1, 0, 0),
		# A mixed pair raises inside the comparison; the deployed rule swallows it.
		([base], ["2026-07-01"], 0, "th", 0, 0, 0),
	]
	for statement_specs, gl_specs, matched, lang, period, too_few, no_match in fixed:
		statement, gl = rows(statement_specs), rows(gl_specs)
		expected = legacy["detect_recon_mismatch"](statement, gl, matched, lang)
		produced = bank_warnings.detect_recon_mismatch(statement, gl, matched, lang)
		assert expected == produced, (statement_specs, gl_specs, matched, lang, expected, produced)
		restated = expected_messages(statement, gl, matched, lang)
		assert produced == [message for _, message in restated], (statement_specs, produced, restated)
		assert [sum(1 for category, _ in restated if category == name)
			for name in ("period", "too_few", "no_match")] == [period, too_few, no_match], (
				statement_specs, restated)
		counts["batches"] += 1
		counts["period"] += period
		counts["too_few"] += too_few
		counts["no_match"] += no_match
	# The documented thresholds, one by one.
	thresholds = [
		(19, 3, 1, False), (20, 3, 1, True), (20, 4, 1, False), (20, 5, 1, False),
		(9, 0, 0, False), (10, 0, 0, False), (20, 20, 0, False),
		# The case the deployed comment cites: twelve ledger rows against a long statement.
		(237, 12, 1, True),
	]
	for statement_count, gl_count, matched, expects_scale in thresholds:
		statement, gl = rows([base] * statement_count), rows([base] * gl_count)
		produced = bank_warnings.detect_recon_mismatch(statement, gl, matched, "en")
		scale = bank_warnings.brv2_warn("gl_too_few", "en", n=gl_count, m=statement_count)
		assert (scale in produced) == expects_scale, (statement_count, gl_count, expects_scale)
		assert produced == legacy["detect_recon_mismatch"](statement, gl, matched, "en")
		counts["batches"] += 1
	return counts


def anchor_cases(legacy):
	values = [None, 0.0, -12.5, 1000.0, 560.25, 12345.67]
	override_sets = (
		(None, None, None, None),
		(990.0, None, None, None),
		(None, 985.0, 498.0, None),
		(0.0, -1.0, 0.0, -0.5),
		(990.0, 985.0, 498.0, 555.0),
	)
	cases = 0
	with_overrides = 0
	for stmt_opening in values:
		for gl_opening in values:
			for gl_closing in values:
				for stmt_closing in values:
					for overrides in override_sets:
						expected = legacy["apply_anchor_overrides"](stmt_opening, gl_opening,
							gl_closing, stmt_closing, *overrides)
						produced = bank_warnings.apply_anchor_overrides(stmt_opening, gl_opening,
							gl_closing, stmt_closing, *overrides)
						assert expected == produced, (stmt_opening, gl_opening, gl_closing,
							stmt_closing, overrides, expected, produced)
						cases += 1
						with_overrides += 1 if produced[5] else 0
	return cases, with_overrides


def run(root=None, source_text=None):
	legacy = load_legacy(root, source_text)
	label_cases, keys = labels_match(legacy)
	detail_cases, floors = completeness_cases(legacy)
	counts = mismatch_cases(legacy)
	anchors, with_overrides = anchor_cases(legacy)
	print({"source": SOURCE_SHA, "label_keys": len(keys), "label_cases": label_cases,
		"detail_cases": detail_cases, "detail_floors": floors, "mismatch": counts,
		"anchor_cases": anchors, "anchor_with_overrides": with_overrides, "differences": 0})
	if (label_cases < 60 or detail_cases < 50 or counts["batches"] < 400
			or counts["period"] < 20 or counts["too_few"] < 20 or counts["no_match"] < 20
			or anchors < 5000 or with_overrides < 1000):
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
