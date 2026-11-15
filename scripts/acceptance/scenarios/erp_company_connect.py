"""验收场景：ERP「从连接添加」把验收账套接成公司 → 刷新让电脑立即重报 → 添加（新建 / 接管空默认公司 / 重复添加不另建）→
主档同步落库（含收款要用的账套资料）→ 停用后从连接重新启用 → 切换当前公司；全程不写账本，账套与基线逐文件一致。

编排 B（实验栈挂候选源码 + 测试机2 的 CC 桌面版 + 验收账套）。模型不参与，费用 0。收款链路的第一段（11-15 上线的那条新用户路）。

**状态：写好未跑**（11-15 晚；Mac 实验栈的测试机一半还没在真机验过，排在 11-16 白天）。首跑要校准的：
- 重报回执的秒数上限（真机 11-15 实测 6–12 秒，这里先按 30 秒）；主档同步的时限（真机约 2–3 分钟，这里等 300 秒）。
- 这一轮走到「添加」的哪一支取决于站点此刻的样子：刚建的空站点走「接管空默认公司」，已有这家公司走「已添加」。
  `acc.erp.company.connect` 哪种站点都能跑（走到的那一支记在证据的 add_branch 里）；`acc.erp.company.connect.fresh` 专守
  「首次添加接管空默认公司」，只在**刚重置的站点**上跑（`maclab.sh reset erp --yes` 后重起），不是就记前置条件不满足、什么都不改。

这条路上 11-15 查出并修掉、这里守住的：
- 首次添加另建一家、当前公司还停在空的默认公司（接管空默认公司，dc86d2d26）→「接管后公司数不变、就是那家默认公司」。
- 已停用的公司重新添加被当成新建（7b25f84d6）→「重新启用同一家、公司数不变」。
- 切换当前公司间歇不生效（默认值缓存被回填旧值，7b25f84d6）→「每次切完，已提交的值 / 缓存的值 / 生效的值三处一致」。
- 账套资料缺收款方式时收款页报「CC 桌面版需更新」（8281e1f40）→「同步后账套资料带收款方式、不算旧读回」。
没在这里守、列在真机清单的：弹窗排布与提示文字（「已更新」）、CC 退出后刷新约 20 秒显示「CC 桌面版未连接」、左上公司下拉入口。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["acc_erp_chain"] = module
        spec.loader.exec_module(module)
    return sys.modules["acc_erp_chain"]


RESCAN_SECONDS = 30   # 首跑校准


def denied(ctx, chain, conf, book):
    """`acc.erp.denied`：没有任何岗位的员工碰收款与「从连接添加」的每个写入口，都被拒、什么都不留、账本不动。"""
    with ctx.step("没岗位的员工逐个入口去试", "每一处都被服务端拒绝；收款记录、公司、写账件一条不多"):
        out = chain.site(ctx, "erp_denied.py", conf, timeout=300)
        ctx.require("这个员工确实没有任何岗位", [], out["roles"])
        ctx.check("每个入口都被拒", {name: "denied" for name in out["outcome"]}, out["outcome"])
        ctx.check("试了八处", 8, len(out["outcome"]))
        ctx.check("收款记录、公司、写账件数都没变", out["before"], out["after"])
    with ctx.step("账本没被动过", "账套与基线逐文件一致"):
        ctx.check("账套 = 基线", True, bool(book.verify(conf["baseline"]).get("ok")))


def run(ctx):
    chain = _chain()
    conf = chain.settings()
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf, writes=False)   # 这一组只读账本
    if ctx.scenario["id"] == "acc.erp.denied":
        return denied(ctx, chain, conf, book)

    with ctx.step("从连接添加", "列表里有验收账套；刷新后电脑重报；添加后是同一家公司，重复添加不另建"):
        fresh = ctx.scenario["id"].endswith(".fresh")
        out = chain.site(ctx, "erp_company_connect.py", {**conf, "reactivate": True, "switch": True, "require_fresh": fresh}, timeout=900)
        before, rescan, add = out["before"], out["rescan"], out["add"]
        ctx.require("有连着的电脑", True, before["connected"])
        ctx.require("列表里有验收账套这一行", True, bool(before["row"]))
        ctx.check("这一行的税号", conf["company_tax"], before["row"]["tax_id"])
        ctx.check("刷新发给了在线的电脑", True, rescan["devices"] >= 1)
        ctx.check("电脑都回执了重报", 0, (rescan["last"] or {}).get("pending"))
        ctx.check(f"重报在 {RESCAN_SECONDS} 秒内回来", True, rescan["seconds"] <= RESCAN_SECONDS)
        ctx.check("重报后这一行还在", True, bool(rescan["row_after"]))
        first, state = add["first"], add["state_before"]
        ctx.facts.update(add_branch="adopted" if first.get("adopted") else "created" if first.get("created") else state)
        if state == "new" and add["blank_default"]:
            ctx.check("首次添加接管了空的默认公司（不另建）", [True, add["blank_default"], before["companies"]],
                      [bool(first.get("adopted")), first["id"], add["companies_after_first"]])
        elif state == "new":
            ctx.check("没有空默认公司时新建一家", [True, before["companies"] + 1], [bool(first.get("created")), add["companies_after_first"]])
        else:
            ctx.check("这家公司已在（已添加 / 已停用）时不另建", before["companies"], add["companies_after_first"])
        if fresh:
            ctx.check("这一轮走的是「接管空默认公司」", "adopted", ctx.facts["add_branch"])
        ctx.check("同一份再添加一次：不新建、不接管、公司数不变",
                  [False, False, add["companies_after_first"], first["id"]],
                  [bool(add["again"].get("created")), bool(add["again"].get("adopted")), add["companies_after_again"], add["again"]["id"]])
        ctx.check("添加后列表里这一行是「已添加」并指向这家公司", ["added", first["id"]],
                  [add["row_after"]["state"], add["row_after"]["company"]])

    with ctx.step("公司与主档", "公司接着 Express、有仓库；主档自己同步到「已同步」，收款要用的账套资料齐"):
        company, sync = out["company"], out["sync"]
        ctx.check("公司的税号 / 启用 / 接 Express", [conf["company_tax"], 1, 1], [company["tax_id"], company["is_active"], company["express_enabled"]])
        ctx.check("公司有仓库", True, company["warehouses"] >= 1)
        ctx.require("主档同步到「已同步」", "ready", sync["states"][-1] if sync["states"] else None)
        ctx.check("账本客户落库", True, sync["customers"] > 0)
        ctx.check("账本物料落库", True, sync["products"] > 0)
        ctx.check("账套资料已存、带收款方式（不算旧读回）", [True, False], [sync["facts"], sync["stale_for_collection"]])
        ctx.check("账套有现金科目（能收现金）", True, sync["cash_account"])
        ctx.facts.update(company=out["name"], nbsp_customers=sync["nbsp_customers"], receipt_types=sync["receipt_types"])

    with ctx.step("停用后重新启用", "停用的公司在列表里标「已停用」，再添加是重新启用同一家"):
        again = out["reactivate"]
        ctx.check("停用后列表里这一行是「已停用」", "inactive", (again["row_inactive"] or {}).get("state"))
        ctx.check("再添加：重新启用、不新建、公司数不变", [True, False, again["companies_before"], out["name"]],
                  [bool(again["result"].get("reactivated")), bool(again["result"].get("created")), again["companies_after"], again["result"]["id"]])
        ctx.check("重新启用后公司启用、列表回到「已添加」", [1, "added"], [again["is_active"], (again["row_after"] or {}).get("state")])

    with ctx.step("切换当前公司", "来回切四次，每次切完已提交的值、缓存的值、生效的值都是刚选的那家"):
        rounds = (out.get("switch") or {}).get("rounds") or []
        ctx.require("切了四次", 4, len(rounds))
        ctx.check("每次三处一致", [[item["target"]] * 3 for item in rounds],
                  [[item["committed"], item["cached"], item["effective"]] for item in rounds])

    with ctx.step("账本没被动过", "接公司、读主档都是只读：账套与基线逐文件一致"):
        ctx.check("账套 = 基线", True, bool(book.verify(conf["baseline"]).get("ok")))
