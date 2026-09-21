# -*- coding: utf-8 -*-
"""断链页换眼重读的仲裁核心（GC-D-2）。

来源：ec2e5c957e6b ``services/recon/bank_stmt_reread.py``。

多页对账单"整份一眼读"会系统性吞掉折行的结算行（SM 2569-05 实例：EDC / K SHOP ฿28,363
两代同位置被吞），而运维用"单页直读"重读同一页就把页内断链补回来了。本模块把那次换眼
自动化：整份读出的行里余额链还有断点时，逐页用**另一只眼**重读，**严格更少断链才采纳**，
否则原样保留（fail-safe，绝不越改越差）。

搬过来的是判定与纪律：① 断链检测复用余额链的现成纯函数（`balance_ok is False` 计数），
不另写一套算法；② 无断链 → 不重读；③ 任何一页失败 → **整轮弃、保原读**（半份重读会丢行，
比原读更差）；④ 每件最多一轮（不递归）；⑤ 采纳与否只比断链数，产出文件级留痕供审计回放。

**没搬的是器件**：fitz 渲染、页数探测、线程池并发、`submit_ctx` 成本归因、环境开关
（`OCR_BANK_CHAIN_REREAD` / 页数上限 / DPI / 并发度）与日志 —— 页数与"读一页"由调用方注入
（`page_count` 与 `read_page(page_number) -> rows`），环境开关也归调用方决定要不要调本函数。
只吃传进来的行与字节，不读文件、不打数据库、不调模型。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from pearnly_cowork.reconciliation.bank_statement import StatementRow, finalize_rows

EYE_VISION = "vision"  # 整份一眼读（pdfplumber / 模型整份读）
EYE_DIRECT = "direct"  # 单页直读

# 页数异常多的巨份跳过换眼，防成本跑飞（旧侧默认值，环境变量归调用方）。
MAX_REREAD_PAGES = 50


def count_chain_breaks(rows: List[StatementRow]) -> int:
	"""余额链断点数 = 逐行核对对不上的动行数（行必须已跑过余额链核对）。"""
	return sum(1 for row in rows if row.balance_ok is False)


def maybe_reread_chain_breaks(rows: List[StatementRow], opening: float, *,
		page_count: int, read_page: Callable[[int], List[StatementRow]],
		extension: str = "pdf", max_pages: int = MAX_REREAD_PAGES,
		) -> Tuple[List[StatementRow], List[Dict]]:
	"""整份读出的行有断链 → 逐页换眼重读，**严格更少断链才采纳**。

	返回 ``(rows, escalations)``：采纳时是重读结果，否则是原读；`escalations` 是一条文件级
	留痕（pages / eye_from / eye_to / breaks_before / breaks_after / kept），供事件审计回放；
	没走到换眼（非 PDF、没断链、页数不合适、页数探测失败）则为空列表。

	只覆盖 PDF：xlsx/csv 直读路零成本零模型不进这条；单图直读本身就是"另一只眼"，不重读。
	整份一眼读丢的是"哪几行"而不是"哪一页"（页级来源在整份解析里已抹平），所以无法把断点
	定位回具体页 —— 有断链就整份逐页换眼，按**文件级**断链数仲裁，与运维手动补读同构。
	"""
	if (extension or "").lower() != "pdf":
		return rows, []
	breaks_before = count_chain_breaks(rows)
	if breaks_before <= 0:
		return rows, []
	if page_count <= 0 or page_count > max_pages:
		return rows, []

	alt_rows: List[StatementRow] = []
	try:
		# 按页号顺序收拢，保持原始行序；任何一页抛错都整轮弃。
		for page_number in range(1, page_count + 1):
			alt_rows.extend(read_page(page_number) or [])
	except Exception:
		return rows, []

	alternative = finalize_rows(alt_rows, opening)
	breaks_after = count_chain_breaks(alternative)
	kept = bool(alternative) and breaks_after < breaks_before
	escalation = {
		"pages": list(range(1, page_count + 1)),
		"eye_from": EYE_VISION,
		"eye_to": EYE_DIRECT,
		"breaks_before": breaks_before,
		"breaks_after": breaks_after,
		"kept": kept,
	}
	if kept:
		return alternative, [escalation]
	return rows, [escalation]
