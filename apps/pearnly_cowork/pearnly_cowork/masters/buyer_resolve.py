# -*- coding: utf-8 -*-
"""买家名/税号 → Cowork 客户 的匹配与归属决策（纯逻辑）。

来源：ec2e5c957e6b ``services/clients/buyer_resolve.py``（438 行）里不碰数据库的那部分
—— 分层匹配（记忆 / 税号 / 名字 / 简称 / 子串）、多页买方冲突判定、以及"税号优先·混合策略"
的归属决策阶梯。

**数据访问是注入的**：旧侧直接开游标查 ``clients`` / ``buyer_to_client_memory`` /
``find_or_create_client_by_tax_id``；原生把这些换成调用方给的值与回调（``memory``、
``clients``、``create_by_tax_id``、``learn``），所以本模块可以完全离线差分。谁提供这些
由存储层与 RPC 层决定（下一步的客户名册）。

保留的判定口径（Zihao 拍板的那几条，别按直觉改）：

· **税号 = 泰国法定唯一身份**：有合法 13 位税号且没命中已存在客户 → 直接按税号建（去重）；
  只靠名字模糊匹配**不建**（防同名异主体错并）；
· 多页买方候选**冲突**（互不包含的两个名字）→ 绝不自动建、转人工（qa_4 那种静默高信心错建）；
· 阈值：≥0.95 自动绑 + 学习；0.80–0.95 标"建议归属"；<0.80 待归属；
· 匹配优先级与置信度（部署版逐条）：记忆完全匹配 1.0 → 税号精确 0.98（税号归一后 ≥10 位才比）
  → 名字精确 0.95 → 简称精确 0.90 → 名字子串 0.80 + 0.10×短/长比 → 简称子串 0.78 + 0.08×比；
  前三级**命中即返**，后两类取最优。
"""

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

# 归属决策的三个阈值（部署版：Zihao 拍板）。
AUTO_ASSIGN_CONFIDENCE = 0.95
SUGGEST_CONFIDENCE = 0.80
# 税号精确匹配要求归一后至少这么多位（部署版：不是 13 位也允许，只是精确匹配）。
TAX_MATCH_MIN_DIGITS = 10
# 合法税号：归一后正好 13 位数字。
VALID_TAX_LENGTH = 13

MatchRecord = Dict[str, Any]
Learn = Callable[[int], Any]
CreateByTax = Callable[[str, str], Optional[int]]


def normalize_candidate(name: Optional[str]) -> str:
    """买方候选的归一：去首尾空格 + 小写（部署版对候选与名字都这么做）。"""
    return (name or "").strip().lower()


def buyer_candidates_conflict(candidates: Optional[Iterable[str]]) -> bool:
    """同一张发票多页是否出现「互不包含」的两个买方名（真冲突）。

    缩写/全称（一个是另一个的子串）视为同一实体，不算冲突；OCR 噪声（大小写/首尾空格）
    归一后比对。用于"同号多页 buyer 候选冲突" → 不自动建客户、转人工确认。
    """
    normalized: List[str] = []
    for candidate in candidates or []:
        value = normalize_candidate(candidate)
        if value and value not in normalized:
            normalized.append(value)
    if len(normalized) <= 1:
        return False
    for first in range(len(normalized)):
        for second in range(first + 1, len(normalized)):
            a, b = normalized[first], normalized[second]
            if a in b or b in a:
                continue
            return True
    return False


def normalize_tax(buyer_tax: Optional[str]) -> str:
    """税号归一：去掉连字符与空格（部署版在比较前做两次同样的清洗）。"""
    return (buyer_tax or "").replace("-", "").replace(" ", "").strip()


def is_valid_tax_id(tax_clean: str) -> bool:
    """合法税号 = 归一后正好 13 位数字；只有它才允许"按税号自动建客户"。"""
    return len(tax_clean) == VALID_TAX_LENGTH and tax_clean.isdigit()


def _substring_confidence(short: str, long: str, base: float, span: float) -> float:
    """子串相似度：较短者占较长者的比例映射到 ``base``–``base + span``。"""
    ratio = min(len(short), len(long)) / max(len(short), len(long))
    return base + (ratio * span)


def match_client(buyer_name: Optional[str], buyer_tax: Optional[str], *,
        memory: Optional[MatchRecord] = None,
        clients: Sequence[MatchRecord] = ()) -> Optional[MatchRecord]:
    """买方 → 客户的匹配，按部署版的层级与置信度返回一条 ``MatchRecord`` 或 ``None``。

    ``memory`` 是调用方按同一作用域查到的学习记忆命中（旧侧的
    ``buyer_to_client_memory`` 查询），``clients`` 是该作用域内**启用中**的客户列表，
    每条至少要有 ``client_id`` / ``name``，可选 ``short_name`` / ``tax_id``。
    返回 ``{client_id, client_name, confidence, match_source}``。
    """
    if not buyer_name or not buyer_name.strip():
        return None
    name = buyer_name.strip()
    tax_clean = normalize_tax(buyer_tax)

    # Layer 1 · 学习记忆完全匹配
    if memory and memory.get("client_id"):
        return {"client_id": int(memory["client_id"]),
                "client_name": memory.get("client_name"),
                "confidence": 1.0,
                "match_source": "memory"}

    # Layer 2 · 税号精确（归一后 ≥10 位才比；13 位唯一）
    if tax_clean and len(tax_clean) >= TAX_MATCH_MIN_DIGITS:
        for client in clients:
            if normalize_tax(client.get("tax_id")) == tax_clean:
                return {"client_id": int(client["client_id"]),
                        "client_name": client.get("name"),
                        "confidence": 0.98,
                        "match_source": "tax_id_exact"}

    # Layer 3–5 · 名字匹配（前两类命中即返，后两类取最优）
    name_lower = name.lower()
    best: Optional[tuple] = None
    for client in clients:
        client_name = (client.get("name") or "").strip()
        short_name = (client.get("short_name") or "").strip()
        if not client_name:
            continue
        client_lower = client_name.lower()
        short_lower = short_name.lower() if short_name else ""

        if client_lower == name_lower:
            return {"client_id": int(client["client_id"]), "client_name": client_name,
                    "confidence": 0.95, "match_source": "name_exact"}
        if short_lower and short_lower == name_lower:
            return {"client_id": int(client["client_id"]), "client_name": client_name,
                    "confidence": 0.90, "match_source": "short_name_exact"}
        if client_lower in name_lower or name_lower in client_lower:
            confidence = _substring_confidence(client_lower, name_lower, 0.80, 0.10)
            if best is None or confidence > best[0]:
                best = (confidence, int(client["client_id"]), client_name, "name_substring")
        if short_lower and (short_lower in name_lower or name_lower in short_lower):
            confidence = _substring_confidence(short_lower, name_lower, 0.78, 0.08)
            if best is None or confidence > best[0]:
                best = (confidence, int(client["client_id"]), client_name, "short_substring")

    if best:
        return {"client_id": best[1], "client_name": best[2],
                "confidence": round(best[0], 3), "match_source": best[3]}
    return None


def resolve_buyer_client(buyer_name: Optional[str], buyer_tax: Optional[str], *,
        memory: Optional[MatchRecord] = None, clients: Sequence[MatchRecord] = (),
        create_by_tax_id: Optional[CreateByTax] = None, learn: Optional[Learn] = None,
        buyer_candidates: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """把"发票买方"解析到一个 Cowork 客户，返回决策 dict（不写任何记录）。

    返回 ``{action, client_id, client_name, confidence, match_source, reason}``，``action``：
     ``assigned``（命中高置信客户，已学习，可放行）、``created``（合法 13 位税号但无匹配 →
     ``create_by_tax_id`` 按税号建/取，已学习）、``suggest``（名字近似但无税号 → 只建议，
     ``learn`` 不调用）、``review``（多页冲突 / 无税号且无匹配 / 建号失败 → 转人工）、
    ``none``（没有买方名）。

    ``create_by_tax_id(tax_clean, buyer_name)`` 与 ``learn(client_id)`` 由调用方注入；
    决策本身与旧侧逐字相同（阈值、顺序、reason 文案）。
    """
    decision: Dict[str, Any] = {"action": "none", "client_id": None, "client_name": None,
                                "confidence": 0.0, "match_source": "", "reason": ""}
    if not buyer_name or not buyer_name.strip():
        decision["reason"] = "no_buyer_name"
        return decision

    if buyer_candidates_conflict(buyer_candidates):
        decision["action"] = "review"
        decision["reason"] = "buyer_candidates_conflict"
        return decision

    tax_clean = normalize_tax(buyer_tax)
    resolved = match_client(buyer_name, buyer_tax, memory=memory, clients=clients)

    # 1) 已存在高置信客户（记忆 / 税号精确 / 名字精确）→ 用它
    if resolved and resolved.get("confidence", 0.0) >= AUTO_ASSIGN_CONFIDENCE \
            and resolved.get("client_id"):
        client_id = int(resolved["client_id"])
        if learn is not None:
            learn(client_id)
        decision.update(action="assigned", client_id=client_id,
                        client_name=resolved.get("client_name"),
                        confidence=float(resolved.get("confidence", 0.0)),
                        match_source=resolved.get("match_source", ""),
                        reason="existing_high_confidence")
        return decision

    # 2) 税号权威：合法 13 位税号但没高置信匹配 → 按税号找或建（去重），闭环放行
    if is_valid_tax_id(tax_clean):
        client_id = create_by_tax_id(tax_clean, buyer_name.strip()) if create_by_tax_id else None
        if client_id:
            if learn is not None:
                learn(int(client_id))
            decision.update(action="created", client_id=int(client_id),
                            client_name=buyer_name.strip(), confidence=0.98,
                            match_source="tax_id_create", reason="auto_created_by_tax_id")
            return decision
        decision.update(action="review", reason="create_by_tax_failed")
        return decision

    # 3) 名字近似但无税号 → 建议归属（不绑/不建），等用户确认
    if resolved and resolved.get("confidence", 0.0) >= SUGGEST_CONFIDENCE \
            and resolved.get("client_id"):
        decision.update(action="suggest", client_id=int(resolved["client_id"]),
                        client_name=resolved.get("client_name"),
                        confidence=float(resolved.get("confidence", 0.0)),
                        match_source=resolved.get("match_source", ""),
                        reason="name_suggestion_no_tax")
        return decision

    # 4) 无税号且无匹配 → 待确认（不建垃圾客户）
    decision.update(action="review", reason="no_tax_no_match")
    return decision
