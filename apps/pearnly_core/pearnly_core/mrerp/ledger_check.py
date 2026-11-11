"""MR.ERP 核账本：我们写过的单此刻在 MR.ERP 列表里还在不在（只读、纯网络，不碰数据库；多产品共用）。

共用入口 `pearnly_core.ledger_tickets.check_present`（A 线 11-10 定稿）按产品 hooks ``pearnly_ledger_check["mrerp"]`` 调到各产品，
产品读好凭据与这份账套读到过的导入菜单后调这里。规矩（D 线 11-10 / 11-11，主会话定）：

- 会话从共用入口拿（`mrerp.account.use`），**不算人点的**（``manual=False``）：账号有被踢标记、被代录占着（产品先判）、拿不到锁、
  核的时候被踢，都不登录去抢、不清标记，整批回空（共用入口记 unknown）；已核出的照回。
- 按**推送记录里的 MR.ERP 单号**查（`docnum`；费用菜单的单号是 MR.ERP 自己编的 PN + 日期 + 随机数，按票号搜不到）：
  ``menu`` 给了先查那个菜单，再按方向查这份账套其余已关联日记账的菜单（进项 aptran；销项 artran、arse）；列表里单号一字不差 = present。
- **gone 先不放开**（:data:`GONE_ENABLED`）：源码核实这三种单没有作废状态、删除即删行、单号改不了（P0 记录「作废 / 删除」），
  条件都满足（每个候选菜单的列表都打得开、搜索每页都正常、翻完、一张都没有）才算删了；上线前对外一律回 unknown，
  真删一张验证过（主会话问用户，不是我们删）再打开。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pearnly_core.session_lock import SessionLockUnavailable
from pearnly_core.mrerp import account as mrerp_account
from pearnly_core.mrerp import menus as mrerp_menus
from pearnly_core.mrerp.push import readback
from pearnly_core.mrerp.session import SessionError

logger = logging.getLogger(__name__)

GONE_ENABLED = False  # 真删一张验证过才打开（主会话 11-11）
LOCK_WAIT_MAX = 60
PAGES_MAX = 10  # 一个单号搜出来的行最多翻几页（30 行一页）；翻不完不下「删了」的结论
PRESENT, GONE = "present", "gone"
# 导入页 → 回读列表模块（同 `push.modules`）；方向 → 可能落到的导入页
LISTING = {"impaptran": "aptran", "impartran": "artran", "imparse": "arse"}
PAGES_FOR = {"purchase": ("impaptran",), "sales": ("impartran", "imparse")}


def linked_menus(menu_options: Any, account_set_key: str, direction: str) -> List[tuple]:
    """这份账套按方向、最近一次读到的已关联日记账的菜单 → ``[(列表模块, 菜单号)]``；没读到过回空。"""
    found = mrerp_menus.options_for(menu_options, account_set_key) or {}
    out = []
    for page in PAGES_FOR.get(direction, ()):
        for option in (found.get("pages") or {}).get(page) or []:
            if isinstance(option, dict) and option.get("linked") and str(option.get("idmenu") or "").strip():
                out.append((LISTING[page], str(option["idmenu"]).strip()))
    return out


def _candidates(item: Dict[str, str], linked: List[tuple]) -> List[tuple]:
    """先查 items 带来的菜单（在已关联的里才认得它属于哪个列表模块），再查其余已关联的。"""
    menu = str(item.get("menu") or "").strip()
    first = [pair for pair in linked if pair[1] == menu]
    return first + [pair for pair in linked if pair not in first]


def _search(session, module: str, docnum: str) -> tuple:
    """在当前菜单的列表里按单号搜 → (找到没有, 搜完整没有)。"""
    for page in range(1, PAGES_MAX + 1):
        response = session.request(
            "POST", f"{module}/component/showdata.php",
            data={"showdata_numrows": str(readback.PAGE_ROWS), "showdata_pages": str(page), "searchdataval": docnum},
            headers={"Referer": f"{session.base}/{module}/allview.php", "X-Requested-With": "XMLHttpRequest",
                     "Origin": session.base})
        if response.status_code != 200:
            return False, False
        text = response.text or ""
        rows = readback.parse_rows(text)
        if any(row.docno == docnum for row in rows):
            return True, True
        if 'id="nodata"' in text or len(rows) < readback.PAGE_ROWS:
            return False, bool(rows) or 'id="nodata"' in text
    return False, False


def _one(session, item: Dict[str, str], linked: List[tuple]) -> Optional[str]:
    candidates = _candidates(item, linked)
    if not candidates:
        return None
    complete = True
    for module, menu in candidates:
        view = session.request("GET", f"{module}/allview.php?idmenu={menu}",
                               headers={"Referer": f"{session.base}/login/mainmenu.php"})
        if view.status_code != 200 or readback.listing_problem(view.text or ""):
            complete = False
            continue
        found, whole = _search(session, module, item["docnum"])
        if found:
            return PRESENT
        complete = complete and whole
    return GONE if GONE_ENABLED and complete else None


def check(lease: Dict[str, str], account_set_key: str, items: List[Dict[str, str]], *, menu_options: Any,
          wait: int) -> Dict[str, Dict[str, str]]:
    """``items``（共用入口整理过的 ``[{doc_id, docnum, direction, menu?}]``）→ ``{doc_id: {"state", "docnum"}}``，只回核出结论的；
    缺的由共用入口补成 unknown。永不抛：读不了就回已核出的（通常是空）。"""
    if not lease.get("username") or not lease.get("password"):
        return {}
    comidyear, _, seldb = str(account_set_key or "").partition(":")
    if not comidyear or not seldb:
        return {}
    out: Dict[str, Dict[str, str]] = {}
    try:
        with mrerp_account.use(lease["system_url"], lease["username"], lease["password"], manual=False,
                               lock_wait=max(1, min(int(wait or 0), LOCK_WAIT_MAX))) as session:
            first = next(iter(mrerp_menus.PAGES.items()))
            session.select(comidyear, seldb, probe=f"{first[0]}/formupload.php?idmenu={first[1]}")
            for item in items:
                state = _one(session, item, linked_menus(menu_options, account_set_key, item.get("direction") or ""))
                if state:
                    out[item["doc_id"]] = {"state": state, "docnum": item["docnum"]}
    except mrerp_account.Marked:
        logger.info("[mrerp-ledger-check] account marked as signed in elsewhere: not checked")
    except SessionLockUnavailable:
        logger.info("[mrerp-ledger-check] account busy: not checked")
    except SessionError as exc:
        logger.info("[mrerp-ledger-check] stopped: %s", exc.code)
    except Exception:  # noqa: BLE001 — 契约：核不了就是 unknown，不冒到调用方
        logger.exception("[mrerp-ledger-check] raised")
    return out


__all__ = ["GONE_ENABLED", "check", "linked_menus"]
