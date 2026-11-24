"""验收场景：DMS 订车真建单（核心集第 15 条）——用 LINE「确认建单」同一个入口在测试账号 DMS-TEST 上真建一张带两行赠品的订车单
→ 独立读回原单（单号、赠品逐行）→ 照 DMS 网页原生「ยกเลิก → ยืนยันยกเลิก」同一请求作废 → 读回作废态。

  acc.dms.line.booking

授权：用户 11-23「可以」（主会话转达）：每次在 testdms 的测试账号 DMS-TEST 上真建一张 → 核对 → 原生作废；只限这个测试账号、
不碰真实租户；**作废失败判不通过、不留测试单**（作废没成时结果里写明单号，由跑的窗口当场按原生方式补作废并报主会话）。

服务端只在测试环境的 testdms 跑（测试机1 实验栈，`testenv.sh lab-start dms` 把 testdms 切过去；导入的测试租户不跑定时任务，
开跑前手动刷新一次车辆快照）。建单走 `book_in_session`（主档复核、顾问归属、建单、回读核对与 LINE 那条一模一样），
LINE 逐问与卡片本身不在这里点（via = "api"）：卡片与回执文字留三星真机记录（`docs/work/2026-11-23-dms-line-gifts.md`）。
不跑识别、不调模型，费用 0；身份证附件不挂（挂附件后赠品不丢由 11-23 真机与 `tests/test_dms_booking_freebies_write.py` 守）。

参数（环境变量）：PEARNLY_ACC_DMS_USER（操作员 Frappe 账号）、PEARNLY_ACC_DMS_HOST（DMS-TEST 主机名，写前核对）、
PEARNLY_ACC_DMS_PEOPLE_ID / _CUSTOMER（已有测试客户）、可选 PEARNLY_ACC_DMS_ENDPOINT、PEARNLY_ACC_DMS_SITE（默认 testdms.pearnly.com）、
PEARNLY_ACC_DMS_LAB=1（testdms 已 lab-start 到测试机，没设就记没测到）、PEARNLY_ACC_DMS_CAR（默认 2，Test 车型：赠品 P001 ยางปัดน้ำฝน 250 + TEST-FB2 100）。

断言：建单成功、有单号；读回原单单号一致、赠品两行按顺序 = 商品 id 4 / 18、数量 1 / 2、金额 250 / 200；作废前表单是这张单的作废态；
作废请求原生成功（200、回包空）；作废后列表里该单状态为「ยกเลิก」、默认（未作废）列表里搜不到。

**状态：写好未跑**（11-24）。
"""
import importlib.util
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet
CANCELLED = "ยกเลิก"
EXPECT = {"ids": ["4", "18"], "qty": ["1", "2"], "amount": ["250", "200"]}


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["acc_erp_chain"] = module
        spec.loader.exec_module(module)
    return sys.modules["acc_erp_chain"]


def settings() -> dict:
    if os.environ.get("PEARNLY_ACC_DMS_LAB") != "1":
        raise Unmet("testdms 没切到测试机实验栈（lab-start dms 由主会话排；切好后设 PEARNLY_ACC_DMS_LAB=1）")
    need = {"user": "PEARNLY_ACC_DMS_USER", "dms_host": "PEARNLY_ACC_DMS_HOST", "people_id": "PEARNLY_ACC_DMS_PEOPLE_ID"}
    missing = [name for name in need.values() if not os.environ.get(name)]
    if missing:
        raise Unmet("缺环境变量：" + "、".join(missing))
    conf = {key: os.environ[name] for key, name in need.items()}
    conf.update(customer_name=os.environ.get("PEARNLY_ACC_DMS_CUSTOMER", ""), endpoint_id=os.environ.get("PEARNLY_ACC_DMS_ENDPOINT", ""),
                car_id=os.environ.get("PEARNLY_ACC_DMS_CAR", "2"), freebies=[[pid, qty] for pid, qty in zip(EXPECT["ids"], EXPECT["qty"])])
    return conf


def numbers(values) -> list:
    try:
        return [Decimal(str(value).replace(",", "") or "x") for value in values or []]
    except Exception:  # noqa: BLE001 —— 读回的不是数就原样比，断言自然不过
        return list(values or [])


def run(ctx):
    chain = _chain()
    conf = settings()
    site = os.environ.get("PEARNLY_ACC_DMS_SITE", "testdms.pearnly.com")
    with ctx.step("用 LINE「确认建单」同一入口在 DMS-TEST 真建单，读回，原生作废，读回作废态",
                  "建单成功；赠品两行 4 / 18、数量 1 / 2；作废请求原生成功；作废后该单状态 ยกเลิก、未作废列表里搜不到"):
        out = chain.site(ctx, "dms_booking.py", {**conf, "run": chain.tag(ctx)}, timeout=900, product="dms", name=site, target="lab")
        ctx.require("写前核对：登录的是登记的测试 DMS", True, out.get("dms_host_ok"))
        booked = out.get("booked") or {}
        ctx.require("建单成功", True, booked.get("ok"))
        booking_no, booking_id = booked.get("booking_no"), booked.get("booking_id")
        ctx.check("建单之后每一步都走完（读回、作废、作废后读回）", "", out.get("after_error") or "")
        ctx.check("有单号", True, bool(booking_no) and bool(booking_id))
        back = out.get("readback") or {}
        arrays = back.get("arrays") or {}
        ctx.check("读回单号一致", booking_no, back.get("docno"))
        ctx.check("读回赠品商品 id（按顺序）", EXPECT["ids"], arrays.get("fbproductval[]"))
        ctx.check("读回赠品数量", numbers(EXPECT["qty"]), numbers(arrays.get("txtfbquantity[]")))
        ctx.check("读回赠品金额", numbers(EXPECT["amount"]), numbers(arrays.get("txtfbamount[]")))
        cancel = out.get("cancel") or {}
        guard = cancel.get("guard") or {}
        ctx.check("作废前表单是这张单的作废态", {"stsel": "c", "idsel": booking_id, "docno": booking_no},
                  {key: guard.get(key) for key in ("stsel", "idsel", "docno")})
        ctx.check("作废请求发出且原生成功（200、回包空）", {"sent": True, "status": 200, "body_len": 0},
                  {key: cancel.get(key) for key in ("sent", "status", "body_len")})
        after = out.get("after") or {}
        cells = [str(cell) for row in after.get("rows") or [] for cell in (row[1] if len(row) > 1 else [])]
        ctx.check("作废后列表里该单状态为 ยกเลิก", True, CANCELLED in cells)
        ctx.check("未作废列表里搜不到这张单", True, str(after.get("active_id") or "") != str(booking_id))
        ctx.save("dms-booking.txt", f"单号 {booking_no}（DMS id {booking_id}）；作废 {'已确认' if CANCELLED in cells else '没核到——要当场补作废'}\n")
