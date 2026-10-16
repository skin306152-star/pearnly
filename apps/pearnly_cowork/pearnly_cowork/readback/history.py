"""科目建议 B（2026-10，用户 10-13 批准）：一本 Express 账套近 12 个月「供应商 → 常用科目」的次数，给推送时要人选的
费用科目排建议（`push.express.account_suggest`）。

- 推送服务经钩子 ``cowork_push_account_history`` 取（:func:`for_push`）；push 域不导入本域（readback 依赖 push），
  与 `cowork_push_fixed_choices` 同一做法。
- 读：经按需请求通道请这条连接的电脑读（``account_history``，`companion.request_service`），不拿写者锁，Express 开着照读。
  结果属于连接与账套（``request_contracts.SHARED_KINDS``）：同一份账套同一时刻只有一件（请求号按连接 + 账套定），谁建的、
  谁来取都行，取的人须此刻能用这条连接。
- 存：站点私有目录 ``private/cowork_account_history/<连接 + 账套的摘要>.json.gz``（不进 File 表、没有下载地址），每份
  账套一份，只存校验过的汇总、读到的时刻和正在等的请求号。
- 什么时候读：要人选费用科目、看「要选的项」时（``refresh``）缺了或超过 ``REREAD_AFTER`` 就请电脑读，照旧先用读过的那份；
  电脑离线、CC 桌面版太旧（不认这种请求）就不建，安静地没有历史建议；读失败 ``RETRY_AFTER_FAILURE`` 内不再请。
  准备推送（``refresh`` 假）只读存着的，不建请求、不写库。
- 很一致的直接用（用户 2026-10-15）：同一供应商近 12 个月 ≥3 张、全记同一个科目（`history_rules.settled`）→ 回
  ``settled``，推送准备当作已记住的选择用（会计员手选过的优先）。公司主档页经钩子 ``cowork_company_history_choices``
  列出这些、``cowork_company_history_forget`` 删：删了记在 ``<摘要>.forgotten.json``（与汇总分开存，重读不会冲掉），
  这家供应商在这份账套里不再按历史用（不自动用、也不按历史建议）。
- 不调模型、不扣费、不写 Express。
"""
import gzip
import hashlib
import json
import os
import uuid
import zlib
from datetime import datetime, timedelta, timezone

import frappe

from pearnly_cowork.companion import request_service
from pearnly_cowork.connections import service as connections
from pearnly_cowork.readback import history_rules as rules
from pearnly_cowork.readback import service as readback
from pearnly_cowork.readback.contracts import ContractError
from pearnly_cowork.targets.identity import normalize_express_account_key

KIND = "account_history"
FOLDER = "cowork_account_history"
READY, READING, NONE = "ready", "reading", "none"
REREAD_AFTER = timedelta(days=1)
RETRY_AFTER_FAILURE = timedelta(hours=1)
# 解压后的汇总最多这么大（防压缩包解开过大）
RAW_MAX_BYTES = 50 * 1024 * 1024
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
FORGOTTEN_MAX = 5000
# 公司主档页每家最多列这么多家供应商
COMPANY_ROWS_MAX = 200


def _now():
    """一律 UTC（裸时间）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stamp(value):
    return value.strftime(_STAMP)


def _at(value):
    try:
        return datetime.strptime(str(value or ""), _STAMP)
    except ValueError:
        return None


def _slot(connection, key):
    return hashlib.sha256(f"{connection}\x1f{key}".encode("utf-8")).hexdigest()[:32]


def _path(slot):
    return frappe.get_site_path("private", FOLDER, slot + ".json.gz")


def _load(slot):
    try:
        with open(_path(slot), "rb") as handle:
            value = json.loads(gzip.decompress(handle.read()).decode("utf-8"))
    except (OSError, ValueError, EOFError):
        return {}
    return value if isinstance(value, dict) else {}


def _save(slot, value):
    path = _path(slot)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = f"{path}.{uuid.uuid4().hex}.part"
    with open(part, "wb") as handle:
        handle.write(gzip.compress(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")))
    os.replace(part, path)


def _forgotten_path(slot):
    return frappe.get_site_path("private", FOLDER, slot + ".forgotten.json")


def _forgotten(slot) -> set:
    try:
        with open(_forgotten_path(slot), encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return set()
    return {str(item) for item in value} if isinstance(value, list) else set()


def _forget(slot, keys) -> bool:
    """记下这些供应商不再按历史用（幂等）；回有没有新增。"""
    known = _forgotten(slot)
    wanted = known | {str(key) for key in keys if key}
    if wanted == known:
        return False
    path = _forgotten_path(slot)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = f"{path}.{uuid.uuid4().hex}.part"
    with open(part, "w", encoding="utf-8") as handle:
        json.dump(sorted(wanted)[:FORGOTTEN_MAX], handle, ensure_ascii=False)
    os.replace(part, path)
    return True


def _gunzip(content):
    try:
        reader = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = reader.decompress(content, RAW_MAX_BYTES + 1)
    except zlib.error as error:
        raise ContractError(rules.INVALID) from error
    if len(raw) > RAW_MAX_BYTES or reader.unconsumed_tail:
        raise ContractError(rules.INVALID)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ContractError(rules.INVALID) from error


def _quiet(fn, *args):
    try:
        return fn(*args)
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return None


def for_push(connection, account_set, seller=None, refresh=False) -> dict:
    """推送服务的钩子（``cowork_push_account_history``）：这张票的卖方（``seller`` ``{"tax", "name"}``）在这本账套里
    近 12 个月常用的科目。回 ``{"state": "ready" | "reading" | "none", "accounts": [{code, docs, last}], "changed"}``：
    ``reading`` 是电脑正在读（读完再看会有）；``changed`` 为真表示建了或了结了请求（调用方提交）。

    ``refresh`` 才会请电脑读、取读好的结果；否则只看存着的。调用方已核过录入权限与这条连接的使用授权。取不到一律回
    ``none``、不抛错：没有历史建议，推送照常问人。"""
    out = {"state": NONE, "accounts": [], "changed": False, "settled": None}
    try:
        key = normalize_express_account_key(account_set)
        if not key or connections.adapter_of(connection) != "express":
            return out
        slot = _slot(connection, key)
        stored = _load(slot)
        taken = _at(stored.get("taken_at"))
        now = _now()
        if refresh and (taken is None or now - taken >= REREAD_AFTER):
            out["state"], out["changed"] = _refresh(connection, key, slot, stored, now)
            stored = _load(slot)
        if out["state"] == NONE and stored.get("suppliers") is not None:
            out["state"] = READY
        seller = seller or {}
        if rules.supplier_key(seller.get("tax"), seller.get("name")) in _forgotten(slot):
            return out  # 主档页删过：这家在这份账套里不再按历史用
        out["accounts"] = rules.supplier_accounts(stored, seller.get("tax"), seller.get("name"))
        out["settled"] = rules.settled(out["accounts"], now.date(), stored.get("taken_at"))
    except Exception:  # noqa: BLE001 — 建议是锦上添花：任何意外都不挡推送
        frappe.log_error(title="Cowork account history failed", message=frappe.get_traceback()[-1500:])
        frappe.clear_messages()
        out.update(state=NONE, accounts=[], settled=None)
    return out


def _express_slots(sets):
    """公司主档给的账套（`companies.service.books` 的行）里 Express 那几份的存放位置。"""
    for item in sets or ():
        if not isinstance(item, dict) or item.get("adapter") != "express":
            continue
        key = normalize_express_account_key(item.get("key"))
        if key and item.get("connection"):
            yield _slot(item["connection"], key)


def company_choices(company_tax, sets) -> list:
    """公司主档页「记住的选择」里来自 Express 历史的那几行（钩子 ``cowork_company_history_choices``）：这家公司各份
    Express 账套里历史很一致、推送时会直接用的「供应商 → 科目」，按供应商合并各年度：
    ``[{supplier_tax, supplier_name, account, account_label, count, last_date}]``（``supplier_tax`` 是供应商键：税号或
    ``name:…``）。只看存着的，不请电脑；调用方已核过录入权限，账套只来自本人能用的连接。"""
    del company_tax  # 归属已由账套定（同一法人各分店、各年度）
    today, merged, names = _now().date(), {}, {}
    for slot in dict.fromkeys(_express_slots(sets)):
        stored, forgotten = _load(slot), _forgotten(slot)
        for key, found in rules.suppliers_of(stored).items():
            if key in forgotten:
                continue
            item = rules.settled(found["accounts"], today, stored.get("taken_at"))
            if not item:
                continue
            names.setdefault(key, found["name"])
            merged.setdefault((key, item["code"]), []).append(item)
    out = []
    for (key, code), items in merged.items():
        # 各年度各自判；不同年度一致到不同科目时各列一行（推送按那一年的账套用）
        out.append({"supplier_tax": key, "supplier_name": names.get(key) or "", "account": code,
                    "account_label": "", "count": sum(item["docs"] for item in items),
                    "last_date": max(item["last"] for item in items)})
    out.sort(key=lambda row: (-row["count"], row["supplier_name"]))
    return out[:COMPANY_ROWS_MAX]


def company_forget(company_tax, supplier, account, sets) -> int:
    """主档页删一条历史学到的（钩子 ``cowork_company_history_forget``）：这家公司各份 Express 账套里这家供应商不再按
    历史用（幂等，重读历史也不会冲掉）。回记下的账套份数。"""
    del company_tax, account
    supplier = str(supplier or "").strip()
    if not supplier or len(supplier) > 400:
        return 0
    return sum(int(_forget(slot, [supplier])) for slot in dict.fromkeys(_express_slots(sets)))


def _refresh(connection, key, slot, stored, now):
    """缺了或读过很久：看正在等的那件，或请电脑再读一次。回 (state, changed)。"""
    pending = stored.get("request")
    if pending:
        current = _quiet(request_service.status, pending)
        if current and current["status"] == "done":
            return _take(slot, stored, pending, now), True
        if current and current["status"] in ("requested", "leased"):
            return READING, False
        if current:  # 失败 / 过期：记下，过一阵再请
            _quiet(request_service.drop, pending)
            stored.update(request=None, failed_at=_stamp(now), error_code=str(current.get("code") or
                                                                              current["status"])[:80])
            _save(slot, stored)
            return NONE, True
        stored["request"] = None  # 请求不在了（被删 / 清掉）：当没请过
    failed = _at(stored.get("failed_at"))
    if failed and now - failed < RETRY_AFTER_FAILURE:
        return NONE, False
    # 科目历史是这份账套的公共资料（ADR-021 第 54 条）：够得到它的电脑谁先来谁读，不按发起人（不传 user）
    if request_service.waiting(connection, KIND, readback._known_version(connection), account_key=key):
        return NONE, False  # 电脑离线、CC 桌面版太旧（不认这种请求）：没有历史建议，不报错、不建
    catalog = _quiet(connections.targets, connection)
    account = _quiet(readback._account, catalog, key) if catalog else None
    if not account:
        return NONE, False
    try:
        params = rules.read_params(account["path"], account["root"], now.date())
    except ContractError:
        return NONE, False
    made = request_service.create(KIND, connection, params, "account_history:" + slot, account_key=account["key"])
    stored.update(request=made["request"], requested_at=_stamp(now))
    _save(slot, stored)
    if made["status"] == "done":  # 别人刚建的同一件已经读完
        return _take(slot, stored, made["request"], now), True
    return READING, True


def _take(slot, stored, request, now):
    """取读好的结果：校验、存下、删掉请求。交回的内容不合规记失败（过一阵再请）。"""
    found = _quiet(request_service.result, request)
    try:
        if found is None:
            raise ContractError(rules.INVALID)
        data = rules.parse(_gunzip(found[0]))
    except ContractError as error:
        stored.update(request=None, failed_at=_stamp(now), error_code=error.code)
        _save(slot, stored)
        _quiet(request_service.drop, request)
        return NONE
    _save(slot, {**data, "taken_at": _stamp(now), "request": None})
    _quiet(request_service.drop, request)
    return READY
