# -*- coding: utf-8 -*-
"""LINE 额度：这一次是**免费回覆**还是**计费推播**（纯逻辑，零外部依赖）。

用户 2026-09-23 原话：「dms line 这边什么场景会消耗 line 官方的这个回话额度？」
「我这个产品未来可能几百人用啊……这个额度我开销不起」「按顺序依次做吧，做完验证真的
覆盖全且不回退」。

这里钉住三件"改错了不会报错、只会悄悄继续花钱或悄悄丢消息"的事：

1. **记账**：每次发送都分得出免费回覆 / 计费推播 / 失败是哪一种 —— 没有它只能猜。
2. **令牌优先**：有令牌先试免费回覆；被明确拒绝（400/401/403）才转推播；超时或 5xx
   （不知道送没送到）也转推播 —— 宁可极小概率重复，也不让消息丢掉。
3. **令牌真的带进后台任务**：只改发送层、不改调用方 = 后台手上没令牌 = 白改。

用法（不需要 frappe、不需要网络；假 frappe / 假 requests 自己注入）：
    PYTHONPATH=apps/pearnly_dms python3 scripts/sample-verify-dms-quota.py
"""

import ast
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))


class FakeCache:
    """够用的假 Redis：hincrby / hgetall / make_key。"""

    def __init__(self):
        self.hash = {}

    def make_key(self, key):
        return key

    def hincrby(self, key, field, amount=1):
        self.hash.setdefault(key, {})
        self.hash[key][field] = int(self.hash[key].get(field, 0)) + int(amount)

    def hgetall(self, key):
        return dict(self.hash.get(key) or {})


class FakeResp:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeRequests:
    """假 requests：按 `mode` 决定回覆是成功 / 被拒 / 5xx / 超时；推播一律成功。"""

    mode = "ok"
    calls = []

    @staticmethod
    def post(url, headers=None, json=None, timeout=None):
        FakeRequests.calls.append({"url": url, "json": json or {}})
        if url.endswith("/message/push"):
            return FakeResp(200, "{}")
        if FakeRequests.mode == "ok":
            return FakeResp(200, "{}")
        if FakeRequests.mode == "rejected":
            return FakeResp(400, "Invalid reply token")
        if FakeRequests.mode == "server":
            return FakeResp(500, "boom")
        if FakeRequests.mode == "timeout":
            raise TimeoutError("read timeout")
        raise AssertionError("unknown mode")


def install_fakes():
    cache = FakeCache()
    fake_frappe = types.ModuleType("frappe")
    fake_frappe.cache = lambda: cache
    fake_frappe.conf = {"pearnly_line_channel_secret": "s",
                        "pearnly_line_channel_access_token": "t"}
    fake_frappe.log_error = lambda **kw: None
    fake_frappe.utils = types.SimpleNamespace(now=lambda: "2026-09-23 10:00:00")
    fake_frappe._ = lambda text: text
    # 模块里还有几个 `@frappe.whitelist(...)` 的入口（webhook 那些）—— 假装饰器直接返回原函数。
    fake_frappe.whitelist = lambda *a, **kw: (lambda fn: fn)
    fake_frappe.flags = types.SimpleNamespace()
    fake_frappe.local = types.SimpleNamespace(site="probe.site")
    fake_frappe.get_hooks = lambda *a, **kw: []
    sys.modules["frappe"] = fake_frappe
    sys.modules["requests"] = FakeRequests
    return cache


def main():
    cache = install_fakes()
    from pearnly_core import line

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def reset():
        FakeRequests.calls.clear()
        FakeRequests.mode = "ok"
        cache.hash.clear()

    def counts():
        return dict(cache.hash.get("pearnly:line:usage") or {})

    # ── 1. 记账：免费的、计费的、以及失败是哪一种，都要分得出来 ─────────────
    reset()
    line.reply("tok", [{"type": "text", "text": "hi"}])
    check("免费回覆记 reply_ok", counts().get("2026-09-23|reply_ok") == 1, counts())

    FakeRequests.mode = "rejected"
    line.reply("tok", [{"type": "text", "text": "hi"}])
    check("令牌被拒单独记 reply_rejected（这就是「窗口到底多长」的答案）",
          counts().get("2026-09-23|reply_rejected") == 1, counts())

    FakeRequests.mode = "timeout"
    line.reply("tok", [{"type": "text", "text": "hi"}])
    check("超时单独记 reply_unknown（不确定送没送到，与被拒分开）",
          counts().get("2026-09-23|reply_unknown") == 1, counts())

    FakeRequests.mode = "server"
    line.reply("tok", [{"type": "text", "text": "hi"}])
    check("5xx 记 reply_failed", counts().get("2026-09-23|reply_failed") == 1, counts())

    FakeRequests.mode = "ok"
    line.push("U1", [{"type": "text", "text": "hi"}])
    line.push("U1", [{"type": "text", "text": "hi"}], retry_key="k1")
    check("推播成功记 push_ok（计费的那一类）",
          counts().get("2026-09-23|push_ok") == 2, counts())

    usage = line.usage()
    check("usage() 能按天读回来（排查与估算都用它）",
          usage.get("2026-09-23", {}).get("reply_ok") == 1
          and usage["2026-09-23"]["push_ok"] == 2, usage)

    # ── 2. 令牌优先：能免费就免费，免费没戏才花钱 ─────────────────────────
    reset()
    ok = line.reply_or_push("U1", [{"type": "text", "text": "card"}], "tok")
    check("有令牌且回覆成功 → 只走回覆、不推播（省下一条额度）",
          ok and len(FakeRequests.calls) == 1
          and FakeRequests.calls[0]["url"].endswith("/message/reply"), FakeRequests.calls)

    reset()
    FakeRequests.mode = "rejected"
    line.reply_or_push("U1", [{"type": "text", "text": "card"}], "tok")
    check("令牌被明确拒绝 → 转推播（免费机会没了，老实花一条）",
          len(FakeRequests.calls) == 2
          and FakeRequests.calls[1]["url"].endswith("/message/push"), FakeRequests.calls)

    reset()
    FakeRequests.mode = "timeout"
    line.reply_or_push("U1", [{"type": "text", "text": "card"}], "tok")
    check("超时（不知道送没送到）→ 仍推播：宁可极小概率重复，也不让消息丢掉",
          len(FakeRequests.calls) == 2
          and FakeRequests.calls[1]["url"].endswith("/message/push"), FakeRequests.calls)

    reset()
    line.reply_or_push("U1", [{"type": "text", "text": "card"}])
    check("没令牌 → 直接推播（老行为不变，不回退）",
          len(FakeRequests.calls) == 1
          and FakeRequests.calls[0]["url"].endswith("/message/push"), FakeRequests.calls)

    # ── 3. 令牌真的带进了后台任务（只改发送层 = 白改） ─────────────────────
    flow_src = (ROOT / "apps/pearnly_dms/pearnly_dms/intake/flow.py").read_text(encoding="utf-8")
    query_src = (ROOT / "apps/pearnly_dms/pearnly_dms/query/query_flow.py").read_text(encoding="utf-8")
    handlers_src = (ROOT / "apps/pearnly_dms/pearnly_dms/line/handlers.py").read_text(encoding="utf-8")
    out_src = (ROOT / "apps/pearnly_dms/pearnly_dms/line/out.py").read_text(encoding="utf-8")

    check("发照片那条路：令牌进了后台任务",
          '_enqueue("image", process_image, binding, line_user_id, message_id, reply_token)'
          in flow_src)
    check("webhook 把令牌交给了发照片那条路",
          "dms_flow.handle_image(" in handlers_src and "reply_token)" in handlers_src)
    check("确认建 / 改档：令牌进了后台任务（attempt 的位置没动）",
          '_enqueue("create", write_create, binding, line_user_id, payload, 1, reply_token)'
          in flow_src
          and '_enqueue("update", write_update, binding, line_user_id, payload, 1, reply_token)'
          in flow_src)
    check("查重与重试链继续往下传令牌",
          'payload.get("endpoint_id"), attempt, reply_token' in flow_src
          and "_enqueue(kind, fn, binding, line_user_id, saved, attempt, reply_token)" in flow_src)
    check("查询：结果看板与畅销榜都带令牌（以前收了参数却丢掉）",
          '_enqueue("records", _run_records, binding, line_user_id, params, reply_token)' in query_src
          and '_enqueue("top", _run_top, binding, line_user_id, params, reply_token)' in query_src
          and "_send(line_user_id, query_cards.sales_board(result), reply_token)" in query_src
          and "_send(line_user_id, query_cards.top_sales_board(result), reply_token)" in query_src)
    check("发送出口走 reply_or_push（不是只看有没有令牌就推播）",
          "line.reply_or_push(line_user_id, [msg], reply_token)" in out_src)

    def params(path, name):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        return None

    for path, fn in (("apps/pearnly_dms/pearnly_dms/intake/flow.py", "process_image"),
                     ("apps/pearnly_dms/pearnly_dms/intake/flow.py", "run_dedup"),
                     ("apps/pearnly_dms/pearnly_dms/intake/flow.py", "write_create"),
                     ("apps/pearnly_dms/pearnly_dms/intake/flow.py", "write_update"),
                     ("apps/pearnly_dms/pearnly_dms/intake/flow.py", "execute"),
                     ("apps/pearnly_dms/pearnly_dms/query/query_flow.py", "_run_records"),
                     ("apps/pearnly_dms/pearnly_dms/query/query_flow.py", "_run_top"),
                     ("apps/pearnly_dms/pearnly_dms/query/query_flow.py", "_run_dimensions")):
        got = params(path, fn)
        check("被入队的 %s 收得下 reply_token（签名对不上会在 worker 里 TypeError）" % fn,
              got is not None and "reply_token" in got, got)

    # ── 4. 一次操作能并成一条就并（额度按条算） ───────────────────────────
    booking_src = (ROOT / "apps/pearnly_dms/pearnly_dms/booking/conversation/"
                   "booking_flow.py").read_text(encoding="utf-8")
    qa_cards_src = (ROOT / "apps/pearnly_dms/pearnly_dms/booking/conversation/"
                    "qa_cards.py").read_text(encoding="utf-8")
    check("汇总卡支持把顺带要说的话并进去（notice=）",
          'nonce: str, notice: str = ""' in qa_cards_src)
    check("主档变了时不再发两条（并进汇总卡，一次操作一条）",
          "notice=qa_cards.TXT_MASTER_CHANGED" in booking_src
          and "_send(line_user_id, qa_cards.master_changed())" not in booking_src)

    failed = [row for row in results if not row[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
