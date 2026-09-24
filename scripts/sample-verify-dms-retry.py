# -*- coding: utf-8 -*-
"""连不上对方 DMS 时的重试与回话（纯逻辑，零 IO）。

用户 2026-09-23 口径（原话）：

    超时第一次失败点加按钮重试，重试同步加时长，第三次还失败同步加重试，再加时长，
    最多三次，还失败就连不上第三方 dms 系统，请联系管理员
    不要加卡片，就文案和 line 官方原生按钮触发

这里钉住三类"改错了不会报错、只会静默变回原样"的东西：

1. **预算**：每次重试把"打开登录页"的上限拉长，第一次就比老的 15 秒宽
   （2026-09-23 生产实测：那台 DMS 的全新 TLS 握手要 24 秒，会话复用才 0.2 秒，
   而 Playwright 每次登录都是新连接 —— 15 秒预算必然超时）。
2. **哪些失败才给重试**：连不上/技术失败给；对方明确拒绝（账号错、被顶下线）不给，
   再点几次还是同样的拒绝。
3. **接线顺序**：`DmsUnreachableError` 必须排在它的父类 `DmsTechnicalError` **之前**
   被捕获、`retry` 动作必须排在消费 nonce 的守卫之前。顺序写反不会报错，
   只会让"连不上"重新变成"我们服务器内部错误"、或者让重试点不动。

用法（本机或容器里的 env python，不需要 frappe）：
    PYTHONPATH=apps/pearnly_dms python3 scripts/sample-verify-dms-retry.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DMS = ROOT / "apps/pearnly_dms/pearnly_dms"


def _tree(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)


def _func(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _called_names(node) -> list:
    """节点里所有被调用函数的名字（`a.b(c)` → `b`）。"""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            target = sub.func
            out.append(target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", ""))
    return out


def _csv_rows(relative: str) -> dict:
    rows = {}
    for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
        if not line or "," not in line:
            continue
        key, _, value = line.partition(",")
        rows[key] = value
    return rows


def main():
    from pearnly_dms.config import pacing as R

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    # ── 1. 预算：三次，一次比一次宽 ──────────────────────────────────────
    check("最多三次（多一次都不给）", R.MAX_ATTEMPTS == 3, R.MAX_ATTEMPTS)
    check("三次的上限各不相同且递增",
          len(R.ATTEMPT_TIMEOUT_MS) == R.MAX_ATTEMPTS
          and all(a < b for a, b in zip(R.ATTEMPT_TIMEOUT_MS, R.ATTEMPT_TIMEOUT_MS[1:])),
          R.ATTEMPT_TIMEOUT_MS)
    check("第一次就比老的 15 秒宽（生产实测新连接握手要 24 秒）",
          R.budget_ms(1) > 15_000, R.budget_ms(1))
    check("三次的上限都够跑完一次真实握手（>24 秒）",
          all(ms > 24_000 for ms in R.ATTEMPT_TIMEOUT_MS), R.ATTEMPT_TIMEOUT_MS)
    check("第 1/2/3 次拿到各自的上限",
          [R.budget_ms(n) for n in (1, 2, 3)] == list(R.ATTEMPT_TIMEOUT_MS))
    check("越界的次数被夹进 1..3（不会 IndexError、也不会白给更长的预算）",
          (R.budget_ms(0), R.budget_ms(-5), R.budget_ms(99)) ==
          (R.budget_ms(1), R.budget_ms(1), R.budget_ms(3)))
    check("次数是脏值（None / 空串 / 非数字）时按第一次算，不炸",
          (R.budget_ms(None), R.budget_ms(""), R.budget_ms("x")) ==
          (R.budget_ms(1), R.budget_ms(1), R.budget_ms(1)))
    check("clamp 自己也是这个口径",
          (R.clamp(0), R.clamp(2), R.clamp(9), R.clamp(None)) == (1, 2, 3, 1))

    # ── 2. 哪些失败才给「重试」 ──────────────────────────────────────────
    check("连不上给重试", R.is_retryable("ERR_DMS_UNREACHABLE"))
    check("技术失败给重试（它自己的文案就是「连接超时，请重试」）",
          R.is_retryable("ERR_DMS_TECHNICAL"))
    check("「我们这边的意外」不给重试（再点还是意外，该直说）",
          not R.is_retryable("ERR_UNEXPECTED"))
    check("对方明确拒绝不给重试（账号错 / 被顶下线，再点还是拒绝）",
          not R.is_retryable("ERR_DMS_AUTH") and not R.is_retryable("ERR_DMS_ADMIN_AUTH")
          and not R.is_retryable("ERR_DMS_CONCURRENT_LOGIN"))
    check("空值/未知码不给重试（不能默认给按钮）",
          not R.is_retryable(None) and not R.is_retryable("") and not R.is_retryable("ERR_WHATEVER"))

    # ── 3. 接线：会话里认得出"连不上"，且预算真的用上 ─────────────────────
    session = _tree("apps/pearnly_dms/pearnly_dms/adapter/session.py")
    parents = {}
    for node in ast.walk(session):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                parents[node.name] = getattr(base, "id", "")
    check("「连不上」是技术失败的子类（上层能靠码区分）",
          parents.get("DmsUnreachableError") == "DmsTechnicalError", parents.get("DmsUnreachableError"))
    login = _func(session, "_perform_login")
    budget_targets = [n.value.id for n in ast.walk(login)
                      if isinstance(n, ast.Assign) and isinstance(n.value, ast.BinOp)] if login else []
    check("打开登录页的预算来自 page_timeout_ms（重试才能把它拉长）",
          any(isinstance(n, ast.Attribute) and n.attr == "page_timeout_ms" for n in ast.walk(login)),
          budget_targets)
    waits = [kw.value for n in ast.walk(login) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") in ("goto", "wait_for_selector")
             for kw in n.keywords if kw.arg == "timeout"]
    check("goto / wait_for_selector 用的是这一档预算，不是写死的默认值",
          len(waits) == 2 and all(isinstance(w, ast.Name) and w.id == "budget" for w in waits),
          [getattr(w, "id", w) for w in waits])

    service = _tree("apps/pearnly_dms/pearnly_dms/adapter/operations.py")
    order = []
    for node in ast.walk(service):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                caught = getattr(handler.type, "id", "")
                if caught in ("DmsUnreachableError", "DmsTechnicalError"):
                    order.append(caught)
    check("捕获顺序：子类在父类之前（写反 → 连不上又会变成「服务器内部错误」）",
          order.index("DmsUnreachableError") < order.index("DmsTechnicalError"), order)
    check("连不上回的是 ERR_DMS_UNREACHABLE（上层按这个码给按钮）",
          '"ERR_DMS_UNREACHABLE"' in (ROOT / "apps/pearnly_dms/pearnly_dms/adapter/operations.py")
          .read_text(encoding="utf-8"))

    flow_source = (ROOT / "apps/pearnly_dms/pearnly_dms/intake/flow.py").read_text(encoding="utf-8")
    flow = ast.parse(flow_source, filename="intake/flow.py")
    retry_branch = [n.lineno for n in ast.walk(flow) if isinstance(n, ast.If)
                    and any(isinstance(c, ast.Attribute) and c.attr == "ACT_RETRY"
                            for c in ast.walk(n.test))]
    nonce_guard = [n.lineno for n in ast.walk(flow) if isinstance(n, ast.Call)
                   and getattr(n.func, "attr", "") == "consume_nonce"]
    check("「重试」分支排在消费 nonce 的守卫之前（否则重试点了没反应）",
          bool(retry_branch) and bool(nonce_guard) and min(retry_branch) < min(nonce_guard),
          (retry_branch, nonce_guard))
    check("重试不消费 nonce（重跑的是同一件事，会话里记着）",
          "consume_nonce" not in "\n".join(
              flow_source.splitlines()[min(retry_branch) - 1:min(nonce_guard) - 1]))

    failed = _func(flow, "_dms_failed")
    gate = [n.lineno for n in ast.walk(failed) if isinstance(n, ast.If)
            and "MAX_ATTEMPTS" in ast.dump(n.test)]
    call = [n.lineno for n in ast.walk(failed) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", "") == "retry_message"]
    check("第三次仍失败 → 不再给按钮（先判次数，再决定要不要发按键）",
          bool(gate) and bool(call) and min(gate) < min(call), (gate, call))
    gate_retryable = [n for n in ast.walk(failed) if isinstance(n, ast.If)
                      and isinstance(n.test, ast.UnaryOp)
                      and getattr(getattr(n.test.operand, "func", None), "attr", "") == "is_retryable"]
    check("不是「连不上」类的失败直接说原文案，不给重试",
          len(gate_retryable) == 1
          and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_push"
                  for c in ast.walk(gate_retryable[0])),
          len(gate_retryable))

    # ── 4. 回话方式：文案 + LINE 原生按键，不是卡片 ───────────────────────
    cards = _tree("apps/pearnly_dms/pearnly_dms/line/cards.py")
    func = _func(cards, "retry_message")
    check("回话函数存在（`flow` 调的就是它）", func is not None)
    returns = [n for n in ast.walk(func) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)] \
        if func else []
    keys = {k.value for k in returns[0].value.keys} if returns else set()
    check("只回「文案 + 快捷按键」，没有卡片骨架（contents/header/footer）",
          keys == {"type", "text", "quickReply"}, sorted(keys))
    type_value = [v.value for k, v in zip(returns[0].value.keys, returns[0].value.values)
                  if getattr(k, "value", "") == "type"] if returns else []
    check("回的是文本消息（LINE 原生文字泡泡）", type_value == ["text"], type_value)
    items = [n for n in ast.walk(func) if isinstance(n, ast.Dict)
             and {getattr(k, "value", "") for k in n.keys} == {"items"}] if func else []
    one = items[0].values[0].elts if items else []
    button = one[0] if len(one) == 1 else None
    check("快捷按键只有一个，且是官方原生按键（postback）",
          len(items) == 1 and len(one) == 1 and isinstance(button, ast.Call)
          and getattr(button.func, "id", "") == "_qr_item",
          len(one))
    check("按键文案与动作码都从常量取（动作码不能翻译，翻就点不动）",
          button is not None and getattr(button.args[0].func, "id", "") == "_t"
          and button.args[0].args[0].value == "BTN_RETRY"
          and getattr(button.args[1].func, "id", "") == "_data"
          and getattr(button.args[1].args[0], "id", "") == "ACT_RETRY")

    th = _csv_rows("apps/pearnly_dms/pearnly_dms/translations/th.csv")
    check("泰文按键文案在语言包里（不是把 key 当按钮发出去）",
          bool(th.get("dms.card.BTN_RETRY")) and th.get("dms.card.BTN_RETRY") != "dms.card.BTN_RETRY",
          th.get("dms.card.BTN_RETRY"))
    for lang in ("th", "en", "zh", "ja"):
        key = "dms.err.ERR_DMS_UNREACHABLE"
        rows = _csv_rows(f"apps/pearnly_dms/pearnly_dms/translations/{lang}.csv")
        check(f"「连不上第三方 DMS 系统，请联系管理员」的 {lang} 文案在包里",
              bool(rows.get(key)) and rows.get(key) != key, rows.get(key))

    # ── 5. 同一套口径覆盖**所有** DMS 入口（不是只照顾一个功能） ────────────
    check("后台不走用户在等的阶梯，一次给足 90 秒",
          R.budget_ms(1, background=True) == R.budget_ms(3, background=True) == R.BACKGROUND_TIMEOUT_MS
          and R.BACKGROUND_TIMEOUT_MS >= R.ATTEMPT_TIMEOUT_MS[-1],
          (R.budget_ms(1, background=True), R.BACKGROUND_TIMEOUT_MS))
    check("后台预算足够跑完一次实测最慢的握手（>24 秒）",
          R.BACKGROUND_TIMEOUT_MS > 24_000, R.BACKGROUND_TIMEOUT_MS)

    # 熔断：阈值前不冷却、到阈值开始冷却、逐级拉长、封顶
    check("没到阈值不冷却（抖一下不算对方挂）",
          [R.cooldown_seconds(n) for n in range(0, R.FAILURE_THRESHOLD)] == [0] * R.FAILURE_THRESHOLD,
          [R.cooldown_seconds(n) for n in range(0, R.FAILURE_THRESHOLD)])
    steps = [R.cooldown_seconds(n) for n in range(R.FAILURE_THRESHOLD, R.FAILURE_THRESHOLD + 4)]
    check("到阈值开始冷却，且逐级拉长直到封顶",
          steps[0] == R.COOLDOWN_STEPS[0] and all(a <= b for a, b in zip(steps, steps[1:]))
          and steps[-1] == R.COOLDOWN_STEPS[-1] and R.cooldown_seconds(99) == R.COOLDOWN_STEPS[-1],
          steps)
    check("冷却有上限（不会一路涨到再也不试）", R.COOLDOWN_STEPS[-1] <= 1800, R.COOLDOWN_STEPS[-1])
    check("脏值就当没失败过（不误挡）",
          R.cooldown_seconds(None) == 0 and R.cooldown_seconds("x") == 0)

    # 预热频率：有人用在追、没人用降频
    check("最近有人用过 → 按 45 秒口径追（点到就是热的）",
          R.should_warm(True, background_max_age=45, age_seconds=60)
          and not R.should_warm(True, background_max_age=45, age_seconds=30))
    check("没人用 → 降到 10 分钟一次（不半夜每分钟敲对方）",
          not R.should_warm(False, background_max_age=45, age_seconds=60)
          and R.should_warm(False, background_max_age=45, age_seconds=R.IDLE_MAX_AGE_SECONDS + 1),
          R.IDLE_MAX_AGE_SECONDS)
    check("从来没刷成功过（age 无穷）也会去刷",
          R.should_warm(False, background_max_age=45, age_seconds=float("inf")))

    # 状态机（用假缓存跑，不连 Redis）
    import types

    store = {}

    class FakeCache:
        def get_value(self, key, use_local_cache=True):
            return store.get(key)

        def set_value(self, key, value, expires_in_sec=None):
            store[key] = value

    fake = types.ModuleType("frappe")
    fake.cache = lambda: FakeCache()
    sys.modules["frappe"] = fake
    try:
        ep = "EP-TEST"
        check("一开始可以试（没有冷却）", R.cooldown_left(ep) == 0)
        for _ in range(R.FAILURE_THRESHOLD):
            R.note_failure(ep, "ERR_DMS_UNREACHABLE")
        check("连着失败到阈值后进入冷却",
              R.cooldown_left(ep) > 0 and R.summary(ep)["failures"] == R.FAILURE_THRESHOLD,
              R.summary(ep))
        R.note_success(ep)
        check("成功一次立刻归零（不把上次的账记到下一个人头上）",
              R.cooldown_left(ep) == 0 and R.summary(ep)["failures"] == 0, R.summary(ep))
        fresh_ep = "EP-TEST-2"
        R.note_failure(fresh_ep, "ERR_DMS_UNREACHABLE")
        check("光失败不算'刚用过'（预热不会因为一直失败就一直追）", not R.used_recently(fresh_ep))
        R.note_success(fresh_ep, mark_used=False)
        check("后台成功不冒充用户刚用过", not R.used_recently(fresh_ep))
        R.mark_used(fresh_ep)
        check("真的用过之后才算'刚用过'", R.used_recently(fresh_ep))
        check("失败不会把'刚用过'抹掉（之前有人真的用过就算）", R.used_recently(ep))
    finally:
        del sys.modules["frappe"]
        store.clear()

    # 接线：所有登录入口都走同一个漏斗，漏斗自己套口径
    service_src = (ROOT / "apps/pearnly_dms/pearnly_dms/adapter/operations.py").read_text(encoding="utf-8")
    check("漏斗里用的是共用口径模块（不是各写一份常量）",
          "from pearnly_dms.config import pacing" in service_src
          and "pacing.budget_ms(" in service_src)
    check("漏斗在登录前先看冷却，冷却期快速失败（不让每个人再白等 30–90 秒）",
          "pacing.cooldown_left(" in service_src and "circuit open" in service_src)
    check("只有「连不上」类失败才记账（账号错/被顶下线是对方好好地拒绝了，不算）",
          service_src.count("pacing.note_failure(") == 2)
    check("连上用完之后归零并记'刚用过'", "pacing.note_success(" in service_src)

    # 全仓：不许有绕开漏斗的登录
    offenders = []
    for path in (ROOT / "apps/pearnly_dms").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.endswith(("adapter/operations.py", "adapter/push.py")):
            continue  # 漏斗本身 + 连接向导的探针（探针也在 build_adapter 里拿了共用默认预算）
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=rel)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "login"):
                offenders.append(f"{rel}:{node.lineno}")
    check("没有绕开漏斗自己登录的地方（这就是'所有功能同一套'的含义）", not offenders, offenders)

    push_src = (ROOT / "apps/pearnly_dms/pearnly_dms/adapter/push.py").read_text(encoding="utf-8")
    check("建适配器时就带上共用默认预算（连接向导/直连入口也吃得到）",
          "pacing.budget_ms(1)" in push_src)
    warm_src = (ROOT / "apps/pearnly_dms/pearnly_dms/adapter/snapshot_sync.py").read_text(encoding="utf-8")
    schedule_src = (ROOT / "apps/pearnly_dms/pearnly_dms/snapshots/schedule.py").read_text(encoding="utf-8")
    hooks_src = (ROOT / "apps/pearnly_dms/pearnly_dms/hooks.py").read_text(encoding="utf-8")
    check("后台采集使用统一登录预算和熔断；前台不承担等待",
          "background=True" in warm_src and "run_logged_in" in warm_src
          and "cooldown_left(" in service_src)
    manual_src = (ROOT / "apps/pearnly_dms/pearnly_dms/snapshots/manual.py").read_text(encoding="utf-8")
    check("只允许显式手动采集，旧定时任务退役，专用队列仍去重",
          "pearnly_dms.snapshots.schedule.sweep" not in hooks_src
          and '"retired": True' in schedule_src
          and 'QUEUE = "dms_sync"' in manual_src
          and "deduplicate=True" in manual_src and "assert_active" in warm_src)
    shared_src = (ROOT / "apps/pearnly_dms/pearnly_dms/adapter/master_shared.py").read_text(encoding="utf-8")
    store_src = (ROOT / "apps/pearnly_dms/pearnly_dms/snapshots/store.py").read_text(encoding="utf-8")
    check("前台旧兼容入口只读完整本地包，失败保旧但不假称新鲜",
          "Directory(endpoint)" in shared_src and "_fetch_masters_via_login" not in shared_src
          and "ERR_DMS_SNAPSHOT_STALE" in store_src and "row.last_error" in store_src)

    # ── 6. 2026-09-23 自查出的两处缺口（改了才知道的） ──────────────────────
    check("点完登录之后的等待窗口也按预算放宽（不只是'打开页面'那一段）",
          "verify_ms = max(self.LOGIN_TIMEOUT_MS" in
          (ROOT / "apps/pearnly_dms/pearnly_dms/adapter/session.py").read_text(encoding="utf-8"))
    check("点了没回应 → 报「连不上」（老口径错报成账号/密码问题，不可重试）",
          "raise DmsUnreachableError(\n            _(\"DMS sign-in did not finish" in
          (ROOT / "apps/pearnly_dms/pearnly_dms/adapter/session.py").read_text(encoding="utf-8")
          .replace("            _(", "            _("))
    check("兜底预算也取自共用口径（不留 15 秒老常量当暗坑）",
          "DEFAULT_PAGE_TIMEOUT_MS = pacing.ATTEMPT_TIMEOUT_MS[0]" in
          (ROOT / "apps/pearnly_dms/pearnly_dms/adapter/session.py").read_text(encoding="utf-8"))
    check("'等多久'这件事有一条**真浏览器**的端到端验证脚本（不只读代码）",
          (ROOT / "scripts/sample-verify-dms-login-budget.py").is_file()
          and (ROOT / "scripts/sample-verify-dms-login-budget.sh").is_file())

    failed_checks = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed_checks), len(failed_checks)))
    print("RESULT: %s" % ("ALL PASS" if not failed_checks else "FAIL"))


if __name__ == "__main__":
    main()
