"""存货科目组：每个账套选一次（用户 2026-09-28）。纯函数，不碰框架与 IO。

科目规则由会计定一次、日常自动套用：存货科目组不是每张票选，而是每个账套选一次，
以后在这个账套新建的库存品都挂这个组。所选的按账套存在连接的 `stock_acc_groups`
（{账套键: {acccod, chosen_by, chosen_at}}），换账套不用清，也不随连接配置保存而丢。

候选只认小助手判过「确是存货科目」的组（fit），推送、选择入口、推送日志同一份名单。
"""
from copy import deepcopy
import json
from typing import Any, Dict, List, Optional

_LABEL_PARTS = ("acccod", "stock_acc", "stock_acc_name")
_MAX_ACCOUNTS = 500


def fit(groups: Any) -> List[Dict[str, Any]]:
    """小助手判为合格（fit）的候选组。"""
    if not isinstance(groups, list):
        return []
    return [g for g in groups if isinstance(g, dict) and g.get("fit") and g.get("acccod")]


def label(group: Dict[str, Any]) -> str:
    """「码 · 科目号 · 科目名」—— 选择下拉、推送日志与导出表同一个拼法。"""
    return " · ".join(str(x) for x in (group.get(k) for k in _LABEL_PARTS) if x)


def choices(raw: Any) -> Dict[str, Dict[str, str]]:
    """连接上存的各账套所选组；坏值当没选过。"""
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items()
            if isinstance(k, str) and isinstance(v, dict) and str(v.get("acccod") or "").strip()}


def chosen(raw: Any, account: str) -> str:
    """这份账套选过的组码；没选过是空串。`account` 须是规范化后的账套键。"""
    return str((choices(raw).get(account) or {}).get("acccod") or "").strip() if account else ""


def choose(raw: Any, account: str, acccod: Any, groups: Any, *, actor: str,
           at: str) -> Optional[Dict[str, Dict[str, str]]]:
    """记下这份账套选的组，返回新的整份记录；不是小助手为这份账套上报的合格组（或记录已满）返回 None。"""
    code = str(acccod or "").strip()
    if not account or not code or code not in {str(g["acccod"]).strip() for g in fit(groups)}:
        return None
    value = deepcopy(choices(raw))
    value[account] = {"acccod": code, "chosen_by": actor, "chosen_at": at}
    return value if len(value) <= _MAX_ACCOUNTS else None


def view(raw: Any, account: str, groups: Any) -> Dict[str, Any]:
    """选择入口要的：这份账套的合格组、选过哪个，以及候选是否已知（小助手还没读过这份账套就是未知）。"""
    return {"account_set_key": account, "known": isinstance(groups, list),
            "groups": [{"acccod": str(g["acccod"]).strip(), "label": label(g), "used_by": g.get("used_by")}
                       for g in fit(groups)],
            "chosen": chosen(raw, account) or None}
