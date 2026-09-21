# -*- coding: utf-8 -*-
"""两法分歧守门（确定性）。

来源：ec2e5c957e6b ``services/recon/bank_arbitration_guard.py``（62 行）。

解析仲裁在"免费解析"与"模型兜底"**都出结果**时只比余额链自洽度（谁更自洽用谁）——
但自洽不等于正确：2026-06-29 的 BBL2645 里模型读出 −114 万、内部首尾相扣、自洽度更低，
被静默选中，真值其实是 0.00。本守门补一层诚实：两法都拿到行、却在期初/期末上显著分歧时，
**不替用户静默选一个**，而是标记需要人工复核。

诚实边界：守门不判"谁对"（两法可能都错），只判"两法对不上 → 别静默相信"。
纯函数：不连模型、不读 IO。
"""

from __future__ import annotations

from typing import List, Optional

# 相对分歧阈：|a−b| / max(|a|,|b|) 超此比例算分歧（5% 吸收四舍五入与极小尾差）。
REL_THRESHOLD = 0.05
# 绝对地板：差值不超过这个金额（泰铢）不算分歧，避免对 0 附近的小额误报。
ABS_FLOOR = 1.0


def diverges(first: float, second: float, rel: float = REL_THRESHOLD,
		abs_floor: float = ABS_FLOOR) -> bool:
	"""两个金额是否显著分歧（一正一负必分歧，不看比例）。"""
	if abs(first - second) <= abs_floor:
		return False
	if (first < 0) != (second < 0):
		return True
	denominator = max(abs(first), abs(second), 1.0)
	return abs(first - second) / denominator > rel


def divergence_reasons(free_opening: Optional[float], free_closing: Optional[float],
		free_row_count: int, model_opening: Optional[float], model_closing: Optional[float],
		model_row_count: int, *, rel: float = REL_THRESHOLD,
		abs_floor: float = ABS_FLOOR) -> List[str]:
	"""两法都出行、期初/期末显著分歧时返回原因列表（空 = 无分歧或无可比）。

	只在两法都拿到行（> 0）时比 —— 一方为空是"兜底接管"，不是"分歧"。
	"""
	if free_row_count <= 0 or model_row_count <= 0:
		return []

	reasons: List[str] = []
	for label, first, second in (("期初", free_opening, model_opening),
			("期末", free_closing, model_closing)):
		if first is None or second is None:
			continue
		if diverges(float(first), float(second), rel, abs_floor):
			reasons.append(f"{label}两法分歧:免费解析 {first} vs Gemini {second} — 需人工核票面")
	return reasons
