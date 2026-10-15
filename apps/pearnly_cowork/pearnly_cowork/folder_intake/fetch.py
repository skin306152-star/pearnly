"""CC 从已授权文件夹列文件、取文件（2026-10 CC 闭环 L2）：经 CC 桌面端的按需请求（`companion.request_service`）
请读这个文件夹的那台电脑做。只读：不识别、不扣费、不登记录入。

- 判据与定时任务表单相同：录入权限 + 读文件夹那条连接的使用授权（`rules._connection`）；根目录必须是那台电脑在
  CC 桌面端里加过、随文件夹同步报上来的（`readers.state`）。读者是（电脑，成员连接）（ADR-021 第 4 批）：请求定向给那台
  电脑（``target_device``），同一个池里别的电脑领不到；只给连接的旧写法按根目录认是哪台（`readers.resolve`）。电脑那边再按本机的根目录核一次（拒绝 `..`、绝对路径、
  符号链接 / 联接点跳出根目录），类型与大小同对话附件（`contracts.FETCH_EXTENSIONS`，30 MB）。
- 那台电脑此刻没来领按需请求（离线），或它的 CC 桌面端还不会领（旧于 `request_contracts.MIN_COMPANION`），就不建请求，
  回 ``{"status": "waiting_for_cc", "reason": "offline" | "update_needed"}``。
- 同一个人、同一段对话（``scope``）对同一份文件 / 同一组条件的请求用固定请求号：还在等、在做、做完没取走时再问一次
  是同一件（先找它，找到就不再问电脑在不在），不会让电脑重复读；取走结果即删（`listing` / `content`），下次再问是新的一件。
"""
import hashlib
import json
import zlib
from datetime import datetime

import frappe
from frappe import _

from pearnly_cowork.companion import request_service
from pearnly_cowork.folder_intake import contracts, readers, rules

# 解压后的清单最多这么大（防小助手交回的压缩包解开过大）。
LISTING_MAX_BYTES = 32 * 1024 * 1024


def _error(error):
    frappe.throw(_(error.code))


def _reader(reader, root_id):
    rules._require()
    try:
        root = contracts.root_id(root_id)
    except contracts.ContractError as error:
        _error(error)
    device, connection = readers.resolve(reader, root)
    doc = rules._connection(connection)
    if not device and readers.members(doc.name):
        # 这条连接上有电脑、却没有一台报过这个根目录：不建不定向的请求（池里谁都能领），也不按部署前按连接记的
        # 在场与租约算在线（第 4 批审查 L2）
        frappe.throw(_("cowork.folder.root_invalid"))
    return doc, device, root, readers.state(device, doc.name)


def waiting(connection, kind, state, device="") -> dict:
    """这台电脑此刻能不能领这种请求（`request_service.waiting`；它没来领过时按文件夹同步报的版本分辨离线与要升级）。"""
    return request_service.waiting(connection, kind, str(state.get("version") or ""), device=device)


def _root_known(state, root):
    if not any(isinstance(row, dict) and row.get("id") == root for row in state.get("roots") or []):
        frappe.throw(_("cowork.folder.root_invalid"))


def _key(kind, reader, params, scope):
    seed = json.dumps([frappe.session.user, reader, params, str(scope or "")], ensure_ascii=False, sort_keys=True)
    return f"{kind}:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:48]


def _reader_key(doc, device):
    """请求号里的读者：认出电脑的是「电脑|连接」（两台电脑各有一个同编号的根目录也是两件），认不出的照旧只是连接。"""
    return contracts.reader_ref(device, doc.name)


def _request(kind, doc, device, params, scope):
    made = request_service.create(kind, doc.name, params, _key(kind, _reader_key(doc, device), params, scope),
                                  target_device=device)
    return {"request": made["request"], "status": made["status"]}


def _open(kind, doc, device, params, scope):
    """同一请求号还在等、在做或做完没取走的那一件：电脑正在读、传它时可能好一阵不来领，不能因此判离线。"""
    found = request_service.find(kind, doc.name, _key(kind, _reader_key(doc, device), params, scope))
    return {"request": found["request"], "status": found["status"]} if found else None


def files(reader, root_id, folder="", *, search="", extensions=None, scope="") -> dict:
    """请电脑列根目录（或其中一个文件夹，含子文件夹）里能取的文件：``{request, status}`` 或 waiting_for_cc。"""
    doc, device, root, state = _reader(reader, root_id)
    try:
        params = {"root_id": root, "folder": contracts.browse_folder(folder),
                  "search": str(search or "").strip()[:contracts.FETCH_SEARCH_MAX],
                  "extensions": contracts.fetch_extensions(extensions), "limit": contracts.FETCH_LIST_MAX}
    except contracts.ContractError as error:
        _error(error)
    found = _open("folder_list", doc, device, params, scope) or waiting(doc.name, "folder_list", state, device)
    if found:
        return found
    _root_known(state, root)
    return _request("folder_list", doc, device, params, scope)


def fetch(reader, root_id, path, *, scope="") -> dict:
    """请电脑交回一份文件：``{request, status}`` 或 waiting_for_cc。"""
    doc, device, root, state = _reader(reader, root_id)
    try:
        params = {"root_id": root, "path": contracts.fetch_path(path)}
    except contracts.ContractError as error:
        _error(error)
    found = _open("folder_fetch", doc, device, params, scope) or waiting(doc.name, "folder_fetch", state, device)
    if found:
        return found
    _root_known(state, root)
    return _request("folder_fetch", doc, device, params, scope)


def state(request) -> dict:
    """请求此刻的状态：{status: requested | leased | done | failed | expired, code}。"""
    rules._require()
    current = request_service.status(request)
    return {"request": current["request"], "status": current["status"], "code": current["code"]}


def _gunzip(content):
    try:
        reader = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = reader.decompress(content, LISTING_MAX_BYTES + 1)
    except zlib.error as error:
        raise contracts.ContractError("cowork.folder.invalid") from error
    if len(raw) > LISTING_MAX_BYTES or reader.unconsumed_tail:
        raise contracts.ContractError("cowork.folder.invalid")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise contracts.ContractError("cowork.folder.invalid") from error


def _local(epoch):
    return datetime.fromtimestamp(epoch, contracts.BANGKOK).strftime("%Y-%m-%d %H:%M")


def listing(request) -> dict:
    """做完的文件清单（取走即删）：``{files: [{path, name, size, modified(曼谷时间), result_folder}], truncated, scanned}``，
    按修改时间新的在前。"""
    rules._require()
    content, _meta = request_service.result(request)
    request_service.drop(request)
    try:
        data = contracts.fetched_listing(_gunzip(content))
    except contracts.ContractError as error:
        _error(error)
    data["files"] = [{**row, "modified": _local(row["modified"])}
                     for row in sorted(data["files"], key=lambda row: (-row["modified"], row["path"]))]
    return data


def content(request) -> tuple:
    """做完的一份文件（文件名, 内容），取走即删。文件名取自请求时给的路径（不信电脑交回的名字）。"""
    rules._require()
    path = str((request_service.status(request).get("params") or {}).get("path") or "")
    data, _meta = request_service.result(request)
    request_service.drop(request)
    try:
        path = contracts.fetch_path(path)
    except contracts.ContractError as error:
        _error(error)
    return path.rsplit("\\", 1)[-1], data


__all__ = ["content", "fetch", "files", "listing", "state", "waiting"]
