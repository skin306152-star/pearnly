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
    from pearnly_dms.intake import retry as R

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
          R.timeout_ms(1) > 15_000, R.timeout_ms(1))
    check("三次的上限都够跑完一次真实握手（>24 秒）",
          all(ms > 24_000 for ms in R.ATTEMPT_TIMEOUT_MS), R.ATTEMPT_TIMEOUT_MS)
    check("第 1/2/3 次拿到各自的上限",
          [R.timeout_ms(n) for n in (1, 2, 3)] == list(R.ATTEMPT_TIMEOUT_MS))
    check("越界的次数被夹进 1..3（不会 IndexError、也不会白给更长的预算）",
          (R.timeout_ms(0), R.timeout_ms(-5), R.timeout_ms(99)) ==
          (R.timeout_ms(1), R.timeout_ms(1), R.timeout_ms(3)))
    check("次数是脏值（None / 空串 / 非数字）时按第一次算，不炸",
          (R.timeout_ms(None), R.timeout_ms(""), R.timeout_ms("x")) ==
          (R.timeout_ms(1), R.timeout_ms(1), R.timeout_ms(1)))
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

    service = _tree("apps/pearnly_dms/pearnly_dms/intake/service.py")
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
          '"ERR_DMS_UNREACHABLE"' in (ROOT / "apps/pearnly_dms/pearnly_dms/intake/service.py")
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

    failed_checks = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed_checks), len(failed_checks)))
    print("RESULT: %s" % ("ALL PASS" if not failed_checks else "FAIL"))


if __name__ == "__main__":
    main()
