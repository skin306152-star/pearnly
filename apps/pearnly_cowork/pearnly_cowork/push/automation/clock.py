"""判定用的日子（数字会计员第 3 期第 17 条第 3 点：测试机上用指定的日子压缩时间，连跑几个会计期间）。

只在**测试租户**由站点配置给：``pearnly_da_today`` = ``"YYYY-MM-DD"``（`scripts/da-clock.py` 设、撤、查）。站点名不合
`pearnly_core.execution.replay.TEST_SITES`（回放开关同一份名单）的一律不认：正式站误配照真实日子判，并记一条错误日志；
值认不出（不是日期）同样当没配。架构检查拦住在仓库的生产配置、部署脚本里写这个键。

认了之后，按它的只有「单据的日子对不对、这家从哪天起算」这几处（都在 Express 推送链上）：

- `today`：自动推前的判定（`push.application.assess`）—— 期间、不晚于今天、不早于上传前 60 天、日月对调（`push.express.automation.
  checks`），上传日也当作这一天（试点的票是现在传的）；同一次判定里的入账方式（`goods_posting`）与预检（`preflight` 的 ``date_future``）；
- `now`：公司升降档记的「自何时」与升降记录的时刻（`companies.automation._set`），之后只认这天以后开始的完整期
  （`automation_rules.evaluate` / `baseline`）。

拿来和库里真实时刻比的照真实时间，不跟它走：扫描只看近 3 天、识别完满 15 分钟的票（`sweep.candidates`，票的创建时刻是真的）；
R1「近 60 天有人改值」（`automation.overruled`，改值记录的时刻是真的，判定与领单重核同一口径）；Express 历史「31 天内读的」
（`automation.conflicts`）；合格回读「截止日之后发起」（`companies.automation`，回读发起时刻是真的——试点里过去各期的截止日都早于
真实今天，回读一律合格，试点步骤要在模拟日子过了截止日之后才发起回读）；判定、提议的记录时刻（``auto_checked_at``、提议的 ``at``，
人推时拿它比之后的选择记录）；月结台账「这个月过完没有」（`companies.ledger`）。人推的准备与「要选的项」（`push.application.service.
prepare` / `choices_view`）也照真实今天：真实今天晚于试点的各期，只会比判定松（不会多出 ``date_future``），判定该停的照样停在判定里。
MR.ERP 不进档位，不跟它走。
"""
from __future__ import annotations

import datetime as dt
from typing import Optional
from zoneinfo import ZoneInfo

import frappe
from pearnly_core.execution.replay import TEST_SITES

CONFIG = "pearnly_da_today"
BANGKOK = ZoneInfo("Asia/Bangkok")
_REFUSED = "pearnly:da-today-refused"


def simulated() -> Optional[dt.date]:
    """测试租户配了且认得出的模拟日子；没配、不是测试租户、认不出回 None。"""
    try:
        value = (frappe.conf or {}).get(CONFIG)
        site = getattr(frappe.local, "site", None)
    except (RuntimeError, AttributeError):  # 没有站点上下文（离线调用）
        return None
    if value in (None, "", "None"):
        return None
    if not isinstance(site, str) or not TEST_SITES.fullmatch(site):
        _misconfigured(site)
        return None
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def today() -> dt.date:
    """判单据日子用的今天（曼谷）：测试租户配了模拟日子就是它，否则真实的今天。"""
    return simulated() or dt.datetime.now(BANGKOK).date()


def now() -> dt.datetime:
    """记升降档时刻用的此刻（站点时区的裸时间，同 ``frappe.utils.now_datetime``）：配了模拟日子时取那一天、钟点照真实的。"""
    real = frappe.utils.now_datetime()
    day = simulated()
    return dt.datetime.combine(day, real.time()) if day else real


def _misconfigured(site):
    try:
        if not frappe.cache.get_value(_REFUSED):
            frappe.cache.set_value(_REFUSED, 1, expires_in_sec=3600)
            frappe.log_error(title="Digital accountant clock refused on a non-test site",
                             message=f"{CONFIG} is set on {site!r}; judging stays on the real date.")
    except Exception:  # noqa: BLE001 — 记不了日志也照真实日子判
        pass


__all__ = ["CONFIG", "now", "simulated", "today"]
