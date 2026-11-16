"""验收场景（一组）：ERP 收款的不正常使用（第 6 批）。每条自己先开一张赊销发票（同 `erp_collection_express.py` 的 open_sale），
再在造出来的现场里收它，最后账本读回、恢复基线。都从服务端函数走（via = "api"），模型不参与，费用 0。

  acc.erp.collection.busy             Express 开着这本账套（M08）：测试机上像 Express 开着窗口那样共享开着 ISRUN 等几张表
                                      （`ledger.py hold`，CC 桌面版写前独占试开就拿不到）→ 网页 / LINE / CC 各收一笔 → 占着时三笔都是
                                      「待写入」、收款员看到的是「Express 开着」那一句（`service.express_waiting`）、账本里一张收款单都没有
                                      → 放开 → 三笔都补写成、各一张 RE
  acc.erp.collection.offline          电脑离线（M07）：停掉测试机上的 CC 桌面版（`win/cc_power.py stop`）→ 三处各收一笔 → 停着时都是
                                      「待写入」、写账件没人领、账本不动 → 起 CC → 都补写成、各只一张 RE
  acc.erp.collection.line.redelivered LINE 同一事件重复投递（M02）：同一个【确认】首投、LINE 补发同一事件号、连点（新事件号同一按钮）
                                      → 只放行一次、只一件写账件、账本只多一张 RE
  acc.erp.collection.line.unbound     LINE 未绑定（M16）：出卡后解绑、用旧卡点【确认】；从没绑过的号点「收款」、打「ยืนยัน」
                                      → 点按钮的只回「请先绑定」、打字的当绑定码回「码不对」；草稿原样、没有写账件、
                                      没有新记录、账本不动
  acc.erp.collection.books            同期两份账套（要先选一次、按选的写）：**留桩**——验收公司只报一本账套（站点脚本的防护就是
                                      「报上来的必须全是验收账套」），要第二本同期账套 + 它的基线 + CC 桌面版报两本，现在造不出；走到记前置条件不满足

编排 B（实验栈 + 测试机2 的 CC 桌面版 + 验收账套）。**状态：写好未跑**（11-16）。
offline 那条会停 / 起测试机2 上的 CC 桌面版：跑前先问主会话（「动测试机先问」），登记里占 testpc2-desktop，与代录不同时跑。
首跑要校准的：占着 / 停着时看多久（watch 90 秒）够不够 CC 去试一次并回报「等账套」；放开后服务端多久再派（settle 300 秒）；
`express_waiting` 只在 CC 回报过等账套之后为真（当天第一笔放行时电脑还没去试过就是假）——看不到就把 watch 拉长。
"""
import importlib.util
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
Unmet = sys.modules["acc_errors"].Unmet

ENTRIES = [{"entry": "web", "cash": "300"}, {"entry": "line", "cash": "200"}, {"entry": "cc", "cash": "150"}]
WATCH = 90        # 占着 / 停着时看多久
HOLD = 300        # 占着多久（看完、拷完现场之后剩下的时间等它自己放开）
BOOKS_GAP = ("验收公司只报一本账套：同期两份账套要先有第二本同期的测试账套（如 69EXP\\TEST 与 TEST2 期间重叠）、它的整本基线、"
             "测试机 CC 桌面版对这家公司报两本，站点脚本的防护再放开成「报上来的都在这两本里」——现在造不出，留桩")


def _load(key: str, path: Path):
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, path)
        sys.modules[key] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[key])
    return sys.modules[key]


def _chain():
    return _load("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")


def misuse(ctx, chain, conf, sale, mode, **params) -> dict:
    return chain.site(ctx, "erp_collection_misuse.py", {**conf, "mode": mode, "customer": sale["expected"]["customer_code"],
                                                         "invoice": sale["state"]["ledger_docnum"], **params},
                      timeout=900, save=f"misuse-{mode}.json")


def held_ledger_untouched(ctx, chain, book, conf, part):
    """现场还在时拷一份：除了刚开的那张发票，账本里一张收款单都没有。"""
    _after, summary = chain.copy_and_diff(ctx, book, conf, chain.chain_tables(), part=part)
    ctx.check("这时账本里只多了那张发票（ARTRN 只多一行）", 1, chain.grew(summary, "ARTRN"))
    ctx.check("这时没有收款明细", 0, chain.grew(summary, "ARRCPIT") or 0)


def submit_and_watch(ctx, chain, conf, sale, why: str) -> list:
    out = misuse(ctx, chain, conf, sale, "submit", entries=ENTRIES, watch=WATCH)
    records = out["records"]
    ctx.require("三处各录了一笔", ["web", "line", "cc"], [item["entry"] for item in records])
    for item in records:
        ctx.check(f"{item['entry']}：{why}时没有写成", False, item["written_seen"])
        ctx.check(f"{item['entry']}：{why}时停在待写入", "queued", item["last"]["status"])
        ctx.check(f"{item['entry']}：一笔只一件写账件", 1, item["last"]["jobs"])
    return records


def settle_and_read(ctx, chain, book, conf, sale, names: list, invoice, total) -> None:
    with ctx.step("补写", "现场放开后三笔都写成，各一张收款单"):
        out = chain.site(ctx, "erp_collection_misuse.py", {**conf, "mode": "settle", "names": names}, timeout=900, save="misuse-settle.json")
        records = out["records"]
        for record in records:
            ctx.require(f"{record['name']} 写成", "written", record["status"])
            ctx.check(f"{record['name']} 回显了收款单号", True, str(record.get("docnum") or "").startswith("RE"))
            ctx.check(f"{record['name']} 名下仍只一件写账件（补写不另建）", 1, record["jobs"])
            chain.only_ledger(ctx, record["book_paths"])
        ctx.check("三张收款单号各不相同", 3, len({record["docnum"] for record in records}))
        ctx.facts.setdefault("docnums", []).extend(record["docnum"] for record in records)
    with ctx.step("账本读回", "发票与三张收款单按单号读得到、余额对得上；预期以外的表逐字节不变"):
        after, summary = chain.copy_and_diff(ctx, book, conf, chain.chain_tables())
        chain.invoice_readback(ctx, book, after, sale, collected=True)
        ctx.check("ARTRN 多的行数 = 一张发票 + 三张收款单", 4, chain.grew(summary, "ARTRN"))
        ctx.check("收款明细多三行", 3, chain.grew(summary, "ARRCPIT") or 0)
        received = 0.0
        for record in records:
            received += float(record["total"])
            chain.receipt_readback(ctx, book, after, record)
        chain.invoice_balance(ctx, book, after, invoice, total, received)


def run(ctx):
    chain = _chain()
    kind = ctx.scenario["id"].removeprefix("acc.erp.collection.")
    if kind == "books":
        raise Unmet(BOOKS_GAP)
    if kind not in ("busy", "offline", "line.redelivered", "line.unbound"):
        raise Unmet(f"场景 {ctx.scenario['id']} 在这个模块里没有对应的走法")
    express = _load("acc_scenario_erp_collection_express", HERE / "erp_collection_express.py")
    conf = chain.settings()
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)
    sale = express.open_sale(ctx, chain, conf)
    invoice, total = sale["state"]["ledger_docnum"], sale["expected"]["totals"]["grand_total"]

    if kind == "busy":
        holder = {}
        thread = threading.Thread(target=lambda: holder.update(out=book.hold(HOLD)), daemon=True)
        with ctx.step("造「Express 开着」", "测试机上共享开着账套的 ISRUN 等几张表，独占试开拿不到"):
            thread.start()
            thread.join(2)   # 一次性计划任务起来要几秒；占用那边出错会马上结束
            busy, deadline = [], time.monotonic() + 120
            while True:
                busy = book.probe().get("busy") or []
                if any(name.upper().endswith("ISRUN.DBF") for name in busy) or not thread.is_alive() or time.monotonic() > deadline:
                    break
                time.sleep(5)
            if not any(name.upper().endswith("ISRUN.DBF") for name in busy):
                raise Unmet(f"占用没造起来（探到被占的：{busy}；占用那边：{holder.get('out')}）")
            ctx.check("账套被占着（ISRUN 打不开）", True, True)
        with ctx.step("占着时收款", "三处各收一笔：都停在待写入、收款员看到「Express 开着」、账本里没有收款单"):
            records = submit_and_watch(ctx, chain, conf, sale, "Express 开着")
            for item in records:
                # 收款员放行后当场那句「Express 开着这本账套」就按这个判（`service.express_waiting`，LINE 回复与 CC 列表同一份）
                ctx.check(f"{item['entry']}：判成在等 Express 关闭", True, item["waiting_seen"])
            held_ledger_untouched(ctx, chain, book, conf, "-held")
        with ctx.step("放开占用", "占用那边到时自己关掉"):
            thread.join(HOLD + 360)
            ctx.require("占用已放开", False, thread.is_alive())
            ctx.save("hold.json", holder.get("out"))
        settle_and_read(ctx, chain, book, conf, sale, [item["name"] for item in records], invoice, total)
        return

    if kind == "offline":
        pc = ctx.targets.windows(chain.HOST)
        with ctx.step("停 CC 桌面版", "测试机上的 CC 桌面版进程没了"):
            stopped = pc.cc("stop", timeout=180)
            ctx.cc_stopped = True
            ctx.save("cc-stop.json", stopped)
            ctx.require("CC 桌面版已停", False, stopped.get("running"))
        with ctx.step("停着时收款", "三处各收一笔：都停在待写入、写账件没人领、账本不动"):
            records = submit_and_watch(ctx, chain, conf, sale, "电脑离线")
            for item in records:
                ctx.check(f"{item['entry']}：写账件没有被领走", False, item["leased_seen"])
            held_ledger_untouched(ctx, chain, book, conf, "-offline")
        with ctx.step("起 CC 桌面版", "进程起来、日志里有起来之后的心跳"):
            started = pc.cc("start", "150", timeout=420)
            ctx.save("cc-start.json", started)
            ctx.require("CC 桌面版起来了、有心跳", [True, True], [bool(started.get("running")), bool(started.get("beat"))])
            ctx.cc_stopped = False
        settle_and_read(ctx, chain, book, conf, sale, [item["name"] for item in records], invoice, total)
        return

    if kind == "line.redelivered":
        with ctx.step("同一个【确认】投三次", "首投放行；LINE 补发同一事件号被跳过；连点（新事件号同一按钮）不回话、不再放行"):
            out = misuse(ctx, chain, conf, sale, "line_redelivered", cash="300")
            first, again, double = out["deliveries"]
            ctx.check("首投领到了事件号并处理", [True, True], [first["claimed"], bool(first["handled"])])
            ctx.check("补发的同一事件号没被再处理", False, again["claimed"])
            ctx.check("连点那次领到了新事件号、但不回话（同一张卡已点过）", [True, []], [double["claimed"], double["replies"]])
            ctx.check("放行之后写账件只一件", 1, out["jobs_after_taps"])
            ctx.check("同意记的是首投那个事件号", "line:" + first["event"], out["consent"])
            record = out["record"]
            ctx.require("这一笔写成", "written", record["status"])
            ctx.check("名下仍只一件写账件", 1, record["jobs"])
            chain.only_ledger(ctx, record["book_paths"])
            ctx.facts.setdefault("docnums", []).append(record["docnum"])
        with ctx.step("账本读回", "只多一张收款单"):
            after, summary = chain.copy_and_diff(ctx, book, conf, chain.chain_tables())
            ctx.check("ARTRN 多的行数 = 一张发票 + 一张收款单", 2, chain.grew(summary, "ARTRN"))
            ctx.check("收款明细只多一行", 1, chain.grew(summary, "ARRCPIT") or 0)
            chain.receipt_readback(ctx, book, after, record)
            chain.invoice_balance(ctx, book, after, invoice, total, float(record["total"]))
        return

    with ctx.step("没绑定的号点收款", "解绑后点旧卡、没绑过的号点收款 / 打确认：只回先绑定 / 码不对，什么都不留"):
        out = misuse(ctx, chain, conf, sale, "line_unbound", cash="300")
        for tap in out["taps"]:
            # 点按钮的两次回「请先绑定」；打字那次没绑定时当绑定码试一次、回「码不对」（`handlers._bind_with_code`）
            ctx.check(f"{tap['event']} 被处理、只回了那一句", [True, [tap["expect"]]], [bool(tap["handled"]), tap["texts"]])
        after = out["after"]
        ctx.check("旧卡的草稿原样（还是草稿、版本没变）", ["draft", out["card"]["version"]], [after["status"], after["version"]])
        ctx.check("没有写账件、没有收款单号、没有记下同意", [0, "", ""], [after["jobs"], after["docnum"], after["consent"]])
        ctx.check("这家公司的收款记录一条没多", out["collections"]["before"], out["collections"]["after"])
        ctx.check("没绑过的号没有留下会话", False, out["stranger_session"])
        ctx.check("草稿最后作废了", "withdrawn", out["record"]["status"])
    with ctx.step("账本读回", "只有那张发票，一张收款单都没有"):
        _after, summary = chain.copy_and_diff(ctx, book, conf, chain.chain_tables())
        ctx.check("ARTRN 只多那张发票", 1, chain.grew(summary, "ARTRN"))
        ctx.check("没有收款明细", 0, chain.grew(summary, "ARRCPIT") or 0)


def cleanup(ctx):
    if getattr(ctx, "cc_stopped", False):   # offline 半路不通过：先把测试机的 CC 桌面版起回来，再恢复基线
        started = ctx.targets.windows(_chain().HOST).cc("start", "150", timeout=420)
        ctx.save("cleanup-cc-start.json", started)
        if not started.get("running"):
            raise RuntimeError(f"测试机的 CC 桌面版没能起回来：{started}")
    _chain().cleanup(ctx)
