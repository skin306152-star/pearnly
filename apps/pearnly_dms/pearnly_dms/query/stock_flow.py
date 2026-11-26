# -*- coding: utf-8 -*-
"""LINE「报表查询 → 1 库存停放报表」：选范围 → 后台用操作员自己的 MR.ERP 账号导出 → 回图片 + 四个下载按钮。

评估 `docs/work/2026-11-26-dms-line-stock-report-assessment.md`，主会话 11-26 批准：

- 用**操作员自己的** MR.ERP 账号（与销售记录同一口径，不绕过 MR.ERP 的分店权限）；0 行回一句事实、不出卡片。
- 全程 reply：选范围后放 LINE 原生加载动画，结果用这次的回复令牌回（`line/out.enqueue` → 共用件 `line_ui.receipt`）；
  第 55 秒还没好回查询那句「正在读取」+【查看结果】。不 push、不定时、不进群。
- 每一步都在服务端再查一次：绑定有效、操作员启用、勾了「允许在 LINE 使用查询功能」（`query_access.can_query`；用户 11-26：
  勾了整个「报表查询」可用，没勾就没有，不另设勾选项）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from urllib.parse import unquote

import frappe

from pearnly_dms import i18n
from pearnly_dms.query import query_access, stock_files, stock_render
from pearnly_dms.query import stock_report as report

logger = logging.getLogger(__name__)

ACT_STOCK_SCOPE = "report_stock_scope"
STOCK_ACTIONS = frozenset({ACT_STOCK_SCOPE})
WAITING_STATE = "query_stock"


def _t(key: str, **kw) -> str:
    return i18n.t(f"dms.card.report.{key}", lang=i18n.line_lang(), **kw)


_SCOPE_DATA = "action=report_stock_scope&scope="  # 产品地图按这串认出入口 line:dms:postback:report_stock_scope


def _quick_item(label: str, scope: str) -> Dict[str, Any]:
    return {"type": "action", "action": {"type": "postback", "label": label, "displayText": label,
                                         "data": _SCOPE_DATA + scope}}


def scope_message() -> Dict[str, Any]:
    """选范围：空车 / 全部 / 未分配团队（文案 + 原生快捷按钮，`dms-line-cards-vs-text` 口径）。"""
    return {"type": "text", "text": _t("TXT_PICK_SCOPE"),
            "quickReply": {"items": [_quick_item(_t("SCOPE_" + scope.upper()), scope) for scope in report.SCOPES]}}


def _reply_text(line_user_id: str, text: str, reply_token: str = "") -> None:
    from pearnly_dms.line import out

    out.push_or_reply_text(line_user_id, text, reply_token)


def open_report(binding: dict, line_user_id: str, reply_token: str) -> None:
    """「报表查询」里点了 1。"""
    if not query_access.can_query(binding):
        _reply_text(line_user_id, i18n.t("dms.card.query.TXT_DENIED", lang=i18n.line_lang()), reply_token)
        return
    from pearnly_dms.line import out

    out.send(line_user_id, scope_message(), reply_token)


def handle_postback(binding: dict, line_user_id: str, reply_token: str, action: str, values: dict) -> None:
    if action != ACT_STOCK_SCOPE:
        return
    scope = str(values.get("scope") or "")
    if not query_access.can_query(binding):
        _reply_text(line_user_id, i18n.t("dms.card.query.TXT_DENIED", lang=i18n.line_lang()), reply_token)
        return
    if scope not in report.SCOPES:
        _reply_text(line_user_id, i18n.t("dms.card.query.TXT_EXPIRED", lang=i18n.line_lang()), reply_token)
        return
    from pearnly_core import line_session
    from pearnly_dms.line import out

    # 在等结果：这时打的字回加载动画，不掉进建档等流程（同销售记录的 `query_results`）；任务结束时退回 `query_menu`
    line_session.set_session(line_user_id, WAITING_STATE, {"scope": scope})
    out.enqueue("stock_report", run, binding, line_user_id, scope, reply_token,
                waiting_text=i18n.t("dms.card.query.MANUAL_RUNNING", lang=i18n.line_lang()))


# ── 后台：读 MR.ERP → 汇总 → 出文件 → 回消息 ──────────────────────────────────
def _endpoint(user_id: str):
    from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

    return resolve_dms_endpoint(user_id, None)


def _car_catalogue(endpoint_name: str) -> Dict[str, Dict[str, str]]:
    """车型代码 → 车名 / 车型名 / 年款（本地快照，不额外读 MR.ERP）；快照没有就空，显示回落。"""
    try:
        from pearnly_dms.snapshots.directory import Directory

        return Directory(endpoint_name).car_catalogue()
    except Exception:
        logger.info("[dms stock report] car catalogue unavailable", exc_info=True)
        return {}


def _filename(headers, fallback: str) -> str:
    """MR.ERP 导出的原文件名（含「-ทั้งหมด」与时间戳）。xlsx 的 filename="…" 是 UTF-8 原字节（按 latin-1 读进来），
    PDF 是 filename*=UTF-8''…；都认不出就用 ``fallback``。"""
    value = ""
    for key, item in (headers or {}).items():
        if str(key).lower() == "content-disposition":
            value = str(item)
    if "filename*=" in value:
        name = value.split("filename*=", 1)[1].split(";", 1)[0].strip()
        if name.lower().startswith("utf-8''"):
            return unquote(name[7:]) or fallback
    if "filename=" in value:
        name = value.split("filename=", 1)[1].split(";", 1)[0].strip().strip('"')
        try:
            name = name.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return name or fallback
    return fallback


def capture(client, adapter, scope: str, catalogue: Dict, as_of: str) -> Dict:
    """``run_logged_in`` 里跑：取表单（MR.ERP 按账号预填的范围）→ 导出 xlsx 与 PDF → 汇总 → 出图与汇总 PDF。只发这三个地址。"""
    transport = adapter.transport()
    base = adapter.base_url

    def read(path, method, data=None):
        if path not in report.READ_PATHS:  # 只读闸：这一块只许碰报表的三个地址
            raise report.ExportChanged("unexpected report path")
        response = transport.get(base + path) if method == "GET" else transport.post(base + path, data=data)
        if response.status_code != 200:
            raise report.ExportChanged(f"{path} returned {response.status_code}")
        return response

    values = report.form_values(read(report.FORM_PATH, "GET").text)
    values["selallocate"] = report.SCOPES[scope]["allocate"]
    files = {}
    for kind, selector in (("xlsx", "1"), ("pdf", "2")):
        response = read(report.EXPORT_PATHS[kind], "POST", {**values, "selreport": selector})
        magic = b"PK" if kind == "xlsx" else b"%PDF"
        if not response.content.startswith(magic):
            raise report.ExportChanged(f"{kind} export is not a file")
        files[kind] = {"content": response.content,
                       "name": _filename(getattr(response, "headers", None), f"{_t('TITLE')}.{kind}")}
    rows = report.parse_export(files["xlsx"]["content"])
    summary = report.summarize(rows, scope, catalogue)
    rendered = None
    if summary["total"]:
        rendered = stock_render.render(adapter.page.context, summary, scope, as_of)
    return {"ok": True, "originals": files, "summary": summary, "rendered": rendered}


def _card(links: Dict[str, str]) -> Dict[str, Any]:
    """一张卡片：标题 + 四个下载按钮（URI 动作；`openExternalBrowser=1` 让手机用系统浏览器下载）。"""
    buttons = []
    for kind, label in (("original_xlsx", "BTN_ORIGINAL_XLSX"), ("original_pdf", "BTN_ORIGINAL_PDF"),
                        ("summary_xlsx", "BTN_SUMMARY_XLSX"), ("summary_pdf", "BTN_SUMMARY_PDF")):
        buttons.append({"type": "button", "style": "link", "height": "sm",
                        "action": {"type": "uri", "label": _t(label), "uri": links[kind] + "&openExternalBrowser=1"}})
    title = _t("TITLE")
    # 标题与按钮同在一个 body 里（没有 footer、没有 spacer / separator）：两块分开时 LINE 给各自的默认内边距，
    # 标题下面空出一大块（真机截图 d6b，主会话 11-26 要收紧）
    return {"type": "flex", "altText": title, "contents": {
        "type": "bubble", "size": "kilo",
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
            {"type": "text", "text": title, "weight": "bold", "wrap": True}, *buttons]}}}


def messages(result: Dict, scope: str, as_of: str, moment) -> List[Dict[str, Any]]:
    """结果 → LINE 消息（≤4 条：≤3 张图 + 1 张卡片）。0 行只回一句事实（主会话 11-26）。"""
    summary = result["summary"]
    if not summary["total"]:
        return [{"type": "text", "text": _t("TXT_EMPTY")}]
    batch = stock_files.new_batch()
    year = moment.year + (543 if i18n.line_lang() == "th" else 0)  # 同「更新时间」：泰文佛历，其它语言公历
    stamp = f"{moment.day:02d}{moment.month:02d}{year}_{moment.hour:02d}{moment.minute:02d}"
    summary_name = f"{_t('TITLE')} [{stamp}]"
    links = {
        "original_xlsx": stock_files.save(result["originals"]["xlsx"]["content"], "xlsx",
                                          result["originals"]["xlsx"]["name"], batch),
        "original_pdf": stock_files.save(result["originals"]["pdf"]["content"], "pdf",
                                         result["originals"]["pdf"]["name"], batch),
        "summary_xlsx": stock_files.save(stock_render.workbook(summary, scope, as_of), "xlsx",
                                         summary_name + ".xlsx", batch),
        "summary_pdf": stock_files.save(result["rendered"]["pdf"], "pdf", summary_name + ".pdf", batch),
    }
    out = []
    for index, image in enumerate(result["rendered"]["images"], 1):
        url = stock_files.save(image, "jpg", f"{summary_name} {index}.jpg", batch)
        out.append({"type": "image", "originalContentUrl": url, "previewImageUrl": url})
    out.append(_card(links))
    return out


def _failure_text(result: Dict) -> str:
    from pearnly_dms.query import query_flow

    return query_flow._failure_text(result)


def run(binding, line_user_id, scope, reply_token=""):
    """后台任务（`line/out.run_guarded` 里跑：绑定变了就不跑；发给本人的消息攒到最后用这次的令牌回）。"""
    try:
        return _run(binding, line_user_id, scope)
    finally:
        _end_wait(line_user_id)


def _end_wait(line_user_id: str) -> None:
    from pearnly_core import line_session

    try:
        if (line_session.get_session(line_user_id) or {}).get("state") == WAITING_STATE:
            line_session.set_session(line_user_id, "query_menu", {})
    except Exception:
        logger.warning("[dms stock report] waiting state not cleared", exc_info=True)


def handle_text(line_user_id: str, sess) -> bool:
    """报表还在出时打的字：放 LINE 原生加载动画，不交给别的流程。"""
    if (sess or {}).get("state") != WAITING_STATE:
        return False
    from pearnly_dms.line.out import start_loading

    start_loading(line_user_id, 60)
    return True


def _run(binding, line_user_id, scope):
    from pearnly_dms.adapter.operations import run_logged_in
    from pearnly_dms.contracts.endpoint import endpoint_name
    from pearnly_dms.line import out

    if not query_access.can_query(binding):
        out.send(line_user_id, {"type": "text", "text": i18n.t("dms.card.query.TXT_DENIED", lang=i18n.line_lang())})
        return {"ok": False, "error_code": "ERR_DMS_QUERY_FORBIDDEN"}
    endpoint = _endpoint(str(binding["user_id"]))
    if not endpoint:
        result = {"ok": False, "error_code": "ERR_NO_CREDS"}
        out.send(line_user_id, {"type": "text", "text": _failure_text(result)})
        return result
    name = endpoint_name(endpoint)
    moment = frappe.utils.now_datetime()
    as_of = stock_render.as_of_label(moment)
    catalogue = _car_catalogue(name)

    def work(client, adapter):
        try:
            return capture(client, adapter, scope, catalogue, as_of)
        except report.ExportChanged as exc:
            logger.warning("[dms stock report] export changed: %s", exc)
            return {"ok": False, "error_code": "ERR_DMS_TECHNICAL"}

    result = run_logged_in(name, work, background=True, read_only_batch=True)
    frappe.db.commit()  # 新事务里再核一次绑定与权限（同 `query_flow._run_records`）
    from pearnly_dms.line.identity import matches

    if not matches(binding, line_user_id):
        return {"ok": False, "cancelled": "binding_changed"}
    if not query_access.can_query(binding):
        out.send(line_user_id, {"type": "text", "text": i18n.t("dms.card.query.TXT_DENIED", lang=i18n.line_lang())})
        return {"ok": False, "error_code": "ERR_DMS_QUERY_FORBIDDEN"}
    if not result.get("ok"):
        out.send(line_user_id, {"type": "text", "text": _failure_text(result)})
        return {"ok": False, "error_code": result.get("error_code")}
    try:
        replies = messages(result, scope, as_of, moment)
        frappe.db.commit()  # LINE 取图前文件要已落库
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="DMS stock report files failed")
        out.send(line_user_id, {"type": "text", "text": _failure_text({"error_code": "ERR_DMS_TECHNICAL"})})
        return {"ok": False, "error_code": "ERR_DMS_TECHNICAL"}
    for message in replies:
        out.send(line_user_id, message)
    return {"ok": True, "total": result["summary"]["total"]}


__all__ = ["ACT_STOCK_SCOPE", "STOCK_ACTIONS", "WAITING_STATE", "capture", "handle_postback", "handle_text", "messages", "open_report", "run",
           "scope_message"]
