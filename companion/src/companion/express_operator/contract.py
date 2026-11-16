# -*- coding: utf-8 -*-
"""代录的约定：入口方式、每张单的状态、结果与原因代码。

前端只按代码显示文案（三语词条在前端），这里不放给人看的句子；``detail`` 只进日志和回看。
异常分三级（评估 §14）：自动（不打扰）/ 自动后报告（汇报里列出）/ 必须人工（这张挂起，出「需要你」卡片，其余照录）。
"""
from __future__ import annotations

# 回执说明（detail）最多多少字：AI 接手的结论原文放最前、整段带给站点（1.12.15 起；截图路径不进说明，走 meta.evidence）
DETAIL_MAX = 2000

ENTRY_MODE = "operator"  # 载荷 entry_mode：direct = 直写（缺省），operator = 代录

# ── 执行目标（ADR-026）：载荷 ``operator.target`` 写录到哪种账本，没写 = Express（载荷 16 / 17 与以前一样）。
# 与共用层 ``pearnly_core/express_operator/contracts.py`` 同值（``tests/test_express_operator_shared.py`` 比对）。
TARGET_EXPRESS = "express"  # Windows 桌面程序（后台消息，``bg_desk``）
TARGET_MRERP = "mrerp"  # 网页（CC 自带网页内核的独立窗口；执行层随第 2 步，之前按「不支持」回执）
TARGETS = (TARGET_EXPRESS, TARGET_MRERP)


def target_of(payload) -> str:
    """代录载荷的执行目标：没写 = Express；写了什么就回什么（认不出的由宿主按「不支持」回执）。"""
    operator = payload.get("operator") if isinstance(payload, dict) else None
    found = operator.get("target") if isinstance(operator, dict) else None
    return str(found or TARGET_EXPRESS)

# 载荷版本号：共用件那一步按合入顺序顺延，现在不占号（主会话 10-26）。接线时改成 payload_contract 里的正式常量。
PAYLOAD_VERSION_PLACEHOLDER = None

# ── 一张单的状态（批次文件里落盘，断点续录据此判断）────────────────────────────
PENDING = "pending"  # 还没开始
ENTERING = "entering"  # 正在录（崩了 / 断电后续录：先读账套判断存没存，不重录）
SAVED = "saved"  # 已存，带 Express 单号
VOIDED = "voided"  # 放弃后已作废（带作废的单号）
WAITING = "waiting"  # 挂起，等人处理（need_human）
SKIPPED = "skipped"  # 人选了跳过 / 预检里取消勾选
DONE_STATES = frozenset({SAVED, VOIDED, SKIPPED})

# ── 批次状态 ──────────────────────────────────────────────────────────────
BATCH_READY = "ready"  # 准备好，等开始（或等指定时刻）
BATCH_RUNNING = "running"
BATCH_PAUSED = "paused"  # 人点了暂停，或电脑被占用 / 锁屏 / 服务器连不上时自动暂停
BATCH_STOPPED = "stopped"  # 人点了停止
BATCH_FINISHED = "finished"

# ── 级别 ──────────────────────────────────────────────────────────────────
AUTO = "auto"
AUTO_REPORTED = "auto_reported"
HUMAN = "human"

# ── 原因代码 → 级别（评估 §14 一行一条）────────────────────────────────────
REASONS = {
    # 必须人工（这张挂起）
    "party_missing": HUMAN,  # 对方主档没有（第 3 期前预检里选「直写建档 / 选已有 / 跳过」）
    "item_missing": HUMAN,  # 商品主档没有
    "tax_setting_mismatch": HUMAN,  # 主档税务设置与票面不符
    "period_locked": HUMAN,  # 单据日期所在期间已关账
    "period_outside": HUMAN,  # 单据日期不在账套期间里（Message#.70）
    "negative_stock": HUMAN,  # 负库存被 Express 拦（4224 / 4091），已占号的按放弃流程作废
    "login_failed": HUMAN,  # 自动登录失败（只试一次，防锁账号）
    "login_missing": HUMAN,  # 这台电脑还没授权登录
    "license": HUMAN,  # Express 授权名额满 / 打不开单据（-330）
    "company_mismatch": HUMAN,  # 打开的公司 / 账套和批次对不上（整批停）
    "company_pick": HUMAN,  # 停在 Express 公司列表、要人选这家（配方没核过或登记表里找不到）
    "account_in_use": HUMAN,  # MR.ERP：这个账号在别处登录，本批被踢下线（不自动重登，整批停）
    "account_set_unavailable": HUMAN,  # MR.ERP：账套打不开（库不存在等，整批停）
    "recipe_mismatch": HUMAN,  # 探路后画面仍和配方对不上（这种单暂不能录）
    "unsupported": HUMAN,  # 这种单还没有配方
    "readback_mismatch": HUMAN,  # 存进去的和草稿不符（不自动改已存的单）
    "unknown_screen": HUMAN,  # 认不出的弹窗 / 画面，AI 也处理不了
    # 单据菜单落到子菜单、里面有几个命令（同一种单分几套单号），载荷的选择与付款方式都定不下来：只停这一张，结果 meta 带
    # menu_options（候选菜单文字）与 doc_type，站点问一次、记住后按 operator.form_choice 再交（不调 AI、不整批停）
    "menu_choice": HUMAN,
    "crash_repeated": HUMAN,  # 同一张崩了两次
    "cc_restarted": HUMAN,  # 录到一半 CC 重启：上次留下的 Express 已按放弃收拾（作废两道保护），这张没录成
    # 保存途中停住（认不出的框、Express 被关）：账套里留了这张的半截单（单头在，金额 / 明细 / 凭证 / 税缺一样或几样）。
    # 不重录、不作废，结果 meta 带 partial {docnum, missing} 与 docnum；Express 还留着等人答框时另带 held {computer, account_set}
    "partial_saved": HUMAN,
    # 中途配方外的业务选择框（1.12.23 ③）：不点、单据窗口不关、Express 留着（held），结果 meta.dialog 带
    # {digest, title, text, buttons, doc_type, stage: editing|saving}；人在对话里选了，站点记住、下回按 operator.dialog_answers 照点
    "dialog_choice": HUMAN,
    # 有人在本批的 Express 里点过 / 打过字（1.12.23 ⑥，移动鼠标、滚轮、切前后台不算）：停手 3 秒后核对画面对不上，或等满 60 秒
    # 人还在点。不交 AI 改人的操作，这张转人工，结果 meta.interrupt 带 {stage, where, what}
    "interrupted": HUMAN,
    "placeholder_left": HUMAN,  # 崩在录明细之后：Express 留了金额 0 的空单头占号，要人作废（不自动改已存的单）
    "network_down": HUMAN,  # 服务器连不上超过等待时间（整批暂停）
    "budget": HUMAN,  # 到了每张 / 每批费用上限
    # 自动后报告
    "duplicate_ref": AUTO_REPORTED,  # 对方单号已存在（1029），照录（推送不查重不拦）
    "voided": AUTO_REPORTED,  # 放弃的单已作废
    "ai_resolved": AUTO_REPORTED,  # AI 接手解决了意外
    "express_restarted": AUTO_REPORTED,  # Express 崩了，已重开续录
    # 自动（暂停后自己恢复）
    "screen_locked": AUTO,  # 锁屏 / 屏幕关
    "user_active": AUTO,  # 有人在用电脑
    "network_wait": AUTO,  # 服务器暂时连不上（-70），先不点、等恢复
    "account_busy": AUTO,  # 别的电脑正在写这本账套（PEARNLY.LCK），等它写完再开始
    "waiting_start": AUTO,  # 指定时刻开始（如下班后），还没到点
}

# 遇到就整批停（不是只挂起这一张）
BATCH_STOPPERS = frozenset({"company_mismatch", "login_failed", "login_missing", "license", "network_down", "budget",
                            "account_in_use", "account_set_unavailable"})


def level(reason: str) -> str:
    return REASONS.get(reason, HUMAN)
