# -*- coding: utf-8 -*-
"""Express 菜单：读到的菜单树 → 命令号，子菜单多个候选时按载荷的选择定一项（纯函数，前台 / 后台做法与测试里的假 Express 共用）。

菜单树（``menu_tree``）：``[{"text": "1. ซื้อ", "cmd": 0, "sub": True, "grey": False, "items": [...]}]``，文字已去掉「&」。

找法（每一层）：先按「数字.」开头找（配方写的路径）；找不到再按文字找：路径这一项本身是文字就按它，是数字就按配方单据标题里的
名字（``HP - ซื้อเงินสด`` 的 ``ซื้อเงินสด``），先全等、再唯一包含。各家公司定制过菜单（11-05 测试机：「2. ซื้อเงินสด」是子菜单、
「4. ซื้อเงินเชื่อ」是命令）时：
- 落到子菜单：里面只有一个可用命令就用它；多于一个不猜，抛 ``MenuAmbiguous``（带候选文字），由引擎按 :func:`pick` 定；
- 路径还没走完却已经是命令：命令文字含单据名才用（这种单在这家是命令、不是子菜单），否则拒绝（交 AI 接手）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from .desk import MenuAmbiguous, Refused

_NUMBER = re.compile(r"^\s*\d+\s*[.)]\s*")
# 转账 / 银行字样（按付款方式选子菜单命令）。英文缩写按整词比，免得撞上别的字
TRANSFER_WORDS = ("โอน", "ธนาคาร", "กสิกร", "ไทยพาณิชย์", "กรุงไทย", "กรุงศรี", "ทหารไทย", "ธนชาต", "ออมสิน")
BANK_CODES = ("bank", "tmb", "ttb", "kbank", "scb", "bbl", "ktb", "bay", "gsb", "uob", "cimb", "lhbank", "kkp", "tisco")
CASH_WORDS = ("เงินสด", "ทั่วไป")
PAY_ROUTES = ("cash", "transfer")


def label(text: str) -> str:
    """去掉菜单项开头的编号（「2. ซื้อโดยโอน…」→「ซื้อโดยโอน…」），给人看、给站点记。"""
    return _NUMBER.sub("", str(text or "").replace("&", "")).strip()


def norm(text: str) -> str:
    return re.sub(r"\s+", "", label(text)).casefold()


def _plain(text) -> str:
    return str(text or "").replace("&", "").strip()


def _usable(items: Sequence[Dict]) -> List[Dict]:
    return [n for n in items or [] if _plain(n.get("text"))]


def _by_text(items: Sequence[Dict], text: str) -> Optional[Dict]:
    want = norm(text)
    if not want:
        return None
    same = [n for n in items if norm(n["text"]) == want]
    if len(same) == 1:
        return same[0]
    near = [n for n in items if want in norm(n["text"])]
    return near[0] if len(near) == 1 else None


def _find(items: Sequence[Dict], key: str, name: str) -> Tuple[Optional[Dict], bool]:
    """回 (这一层找到的项, 是不是按编号找到的)。"""
    items = _usable(items)
    if key.isdigit():
        for node in items:
            if _plain(node["text"]).startswith(f"{key}."):
                return node, True
    return _by_text(items, name if key.isdigit() else key), False


def _is_command(node: Dict) -> bool:
    return not node.get("sub") and int(node.get("cmd") or 0) not in (0, 0xFFFFFFFF)


def resolve(tree: Sequence[Dict], path: Sequence[str], name: str = "") -> Tuple[int, bool]:
    """回 (命令号, 是否原样按路径的编号一层层找到)。找不到 / 对不上抛 ``Refused``，子菜单多候选抛 ``MenuAmbiguous``。"""
    path = [str(k).strip() for k in path if str(k).strip()]
    if not path:
        raise Refused("empty menu path")
    items, literal = list(tree or []), True
    for depth, key in enumerate(path):
        if not items:
            raise Refused("menu not found")
        node, by_number = _find(items, key, name)
        if node is None:
            raise Refused(f"menu item {key} not found")
        literal = literal and by_number
        last = depth == len(path) - 1
        if not node.get("sub"):
            if not _is_command(node):
                raise Refused(f"menu item {key} has no command")
            if last:
                return int(node["cmd"]), literal
            if name and norm(name) in norm(node["text"]):
                return int(node["cmd"]), False  # 这家这种单是命令、不是子菜单（例如「4. ซื้อเงินเชื่อ」）
            raise Refused(f"menu item {key} is a command, the path goes on")
        if last:
            commands = [n for n in _usable(node.get("items") or []) if _is_command(n)]
            usable = [n for n in commands if not n.get("grey")] or commands
            if len(usable) == 1:
                return int(usable[0]["cmd"]), False
            if not usable:
                raise Refused(f"submenu {key} has no command")
            raise MenuAmbiguous([label(n["text"]) for n in usable], path)
        items = list(node.get("items") or [])
    raise Refused("empty menu path")


def _transfer(text: str) -> bool:
    low = str(text or "").casefold()
    if any(word in low for word in TRANSFER_WORDS):
        return True
    return any(re.search(rf"(?<![a-z]){code}(?![a-z])", low) for code in BANK_CODES)


def _cash(text: str) -> bool:
    return any(word in str(text or "") for word in CASH_WORDS) and not _transfer(text)


def pick(options: Sequence[str], form_choice: str = "", pay_route: str = "") -> str:
    """子菜单多个命令时选哪一个：先用站点记住的菜单文字（规范化后全等，或互相包含且唯一）；再按付款方式（现金 → 含
    「เงินสด / ทั่วไป」且不是转账 / 银行的唯一一项；转账 → 含「โอน」或银行字样的唯一一项）。定不下来回空串（问人）。"""
    options = [str(o) for o in options or []]
    want = norm(form_choice)
    if want:
        same = [o for o in options if norm(o) == want]
        if len(same) == 1:
            return same[0]
        near = [o for o in options if norm(o) and (want in norm(o) or norm(o) in want)]
        if len(near) == 1:
            return near[0]
    route = str(pay_route or "").strip().lower()
    hits = [o for o in options if _cash(o)] if route == "cash" else \
        [o for o in options if _transfer(o)] if route == "transfer" else []
    return hits[0] if len(hits) == 1 else ""


def summary(tree: Sequence[Dict], focus: str = "", limit: int = 4000) -> str:
    """给 AI 看的菜单（缩进表示层级，「▸」是子菜单，「(灰)」是灰掉的）。``focus``：只展开顶层这一项（配方路径的第一层），
    其余顶层项只列名字；找不到这一项就全部展开。"""
    tree = _usable(tree)
    hit = lambda n: bool(focus) and _plain(n["text"]).startswith(f"{focus}.")  # noqa: E731
    open_all = not any(hit(n) for n in tree)
    lines: List[str] = []

    def walk(items, depth, expand):
        for node in _usable(items):
            lines.append("  " * depth + _plain(node["text"]) + (" ▸" if node.get("sub") else "")
                         + (" (灰)" if node.get("grey") else ""))
            if node.get("sub") and expand:
                walk(node.get("items") or [], depth + 1, True)

    for node in tree:
        walk([node], 0, open_all or hit(node))
    return "\n".join(lines)[:limit]
