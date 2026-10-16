"""推后核对的服务端一半（数字会计员第 3 期第 6 条）：只防写入走样与挂错对方，不防判错。纯函数，不连库。

- CC 桌面版（≥ 1.8.0，`companion/src/companion/write_verify.py`）写完拿手上的载荷比本次写入行，回执带
  ``meta.verify {state: ok | mismatch, fields: [...], party: {tax_id, branch}}``（``party`` 是实际挂到的对方档的税号与分店）。
- 服务端照收 ``state``，另拿**票面**的对方税号 / 分店（准备推送时冻结进目标的 ``party_face``，不取回执里的 ``party_code``）比
  ``party``，不一致也算 mismatch（``party_tax`` / ``party_branch``）。
- 旧版 CC 桌面版不带 ``verify`` → ``unverified``，由月度回读覆盖（自动推不会派给旧版）。
- 结果写推送记录 ``verify_state`` / ``verify_json``；mismatch 进异常清单 ``written_mismatch``，照「推送不查重不拦」不回滚、不重推，
  自动推的另立刻降到 L0。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from pearnly_cowork.push.express.choices import branch5, digits

OK, MISMATCH, UNVERIFIED = "ok", "mismatch", "unverified"
STATES = (OK, MISMATCH, UNVERIFIED)
# CC 桌面版回的不符项（`write_verify.FIELDS`）加服务端自己比出的两项
COMPANION_FIELDS = ("party_code", "party_master", "account", "date", "rectyp", "refnum", "vat_tax_id", "vat_branch",
                    "pos_date", "unreadable")
FIELDS = (*COMPANION_FIELDS, "party_tax", "party_branch")
TAX_MAX, BRANCH_MAX = 20, 5


def face(direction: Any, fields: Mapping[str, Any]) -> Dict[str, str]:
    """票面上的对方：进项看卖方、销项看买方；税号只认 13 位，分店只认票面印了的（数字或「总公司」字样）。"""
    # 只在准备推送时用：`common.sanitize_push_meta` 也导入本模块，那条路不带 pearnly_core（账套比对要它）
    from pearnly_cowork.targets.account_match import printed_branch

    side = "buyer" if direction == "sales" else "seller"
    fields = fields if isinstance(fields, Mapping) else {}
    tax = digits(fields.get(f"{side}_tax") or fields.get(f"{side}_tax_id"))
    return {"tax_id": tax if len(tax) == 13 else "", "branch": printed_branch(fields.get(f"{side}_branch"))}


def clean(raw: Any) -> Optional[Dict[str, Any]]:
    """回执里的 ``verify`` 只留白名单（`common.sanitize_push_meta` 用）：认不出 ``state`` 回 None。"""
    if not isinstance(raw, Mapping) or raw.get("state") not in (OK, MISMATCH):
        return None
    fields = raw.get("fields") if isinstance(raw.get("fields"), list) else []
    party = raw.get("party") if isinstance(raw.get("party"), Mapping) else {}
    kept = [name for name in COMPANION_FIELDS if name in {str(item) for item in fields}]
    out_party = {}
    for key, limit in (("tax_id", TAX_MAX), ("branch", BRANCH_MAX)):
        if key in party:
            out_party[key] = str(party.get(key) or "").strip()[:limit]
    return {"state": raw["state"], "fields": kept, "party": out_party}


def outcome(verify: Any, party_face: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """一次成功写入的核对结论 ``{state, fields, party}``（见文件头）。"""
    found = clean(verify)
    if found is None:
        return {"state": UNVERIFIED, "fields": [], "party": {}}
    differ = list(found["fields"])
    wanted = party_face if isinstance(party_face, Mapping) else {}
    party = found["party"]
    tax = digits(wanted.get("tax_id"))
    if len(tax) == 13 and "tax_id" in party and digits(party.get("tax_id")) != tax:
        differ.append("party_tax")
    branch = str(wanted.get("branch") or "")
    if branch and str(party.get("branch") or "") and branch5(party["branch"]) != branch5(branch):
        differ.append("party_branch")
    if found["state"] == MISMATCH and not differ:
        differ.append("unreadable")
    return {"state": MISMATCH if differ else OK, "fields": [name for name in FIELDS if name in differ],
            "party": party}


__all__ = ["FIELDS", "MISMATCH", "OK", "STATES", "UNVERIFIED", "clean", "face", "outcome"]
