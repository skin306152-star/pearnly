"""Publish versioned menus and reconcile them against current binding/query access.

Publishing is an explicit deployment operation. Permission hooks only link already
published menus; they cannot replace the OA's default during an unfinished release.

语言（用户 2026-09-27，与 ERP 一致）：每个已绑定用户挂**他手机上 LINE 语言**的那一套
（泰 / 英 / 中 / 日 × 基础 / 带查询，共 8 套）；手机换了语言，下一次发消息时重挂。
泰文用设计原图，其余语言的图由 `scripts/render-dms-line-menus.py` 生成。官方号默认菜单
（未绑定的人看到的）仍是泰文基础菜单。只发布过旧的两套（`basic` / `query`，泰文）时，
照旧挂那两套，不在半路改官方号。
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import frappe
import requests

from pearnly_core import line, line_binding
from pearnly_dms import i18n
from pearnly_dms.line import cards
from pearnly_dms.menu.links import account_action
from pearnly_dms.query.query_access import can_query

CONFIG = "pearnly_dms_rich_menus"
LANGUAGES = i18n.LANGUAGES
VARIANTS = ("basic", "query")
_APPLIED = "pearnly:dms:line-menu:"

def _state_store():
    """不淘汰的状态存储（``pearnly_core.execution.state``；用到时才导入，离线检查导入本模块不需要共用层）。"""
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()



def key(language, variant) -> str:
    return f"{language}:{variant}"


def _request(method, path, *, body=None, image=None, missing_ok=False):
    token = frappe.conf.get("pearnly_line_channel_access_token")
    if not token:
        raise RuntimeError("LINE channel unavailable")
    headers = {"Authorization": "Bearer " + token}
    origin = "https://api-data.line.me" if image is not None else "https://api.line.me"
    if image is not None:
        headers["Content-Type"] = "image/png"
    response = requests.request(method, origin + "/v2/bot/" + path, json=body,
                                data=image, headers=headers, timeout=20)
    if response.status_code == 404 and missing_ok:
        return {}
    if response.status_code != 200:
        # LINE 把原因放在响应体里（例如 "An image has already been uploaded to the richmenu"）。
        # 只报状态码会让排查从"看一句话"变成"猜半天"，所以带上正文（截断）。
        raise RuntimeError(f"LINE rich menu HTTP {response.status_code}: {response.text[:200]}")
    return response.json() if response.content else {}


def _has_image(menu_id):
    """这个菜单现在有没有图片。LINE 没有"是否已上传"的字段，只能读一次内容：200 = 有。

    为什么必须问一次：LINE 拒绝对**已经有图片**的菜单再次上传
    （400 "An image has already been uploaded to the richmenu"）。而 `publish()` 是幂等的，
    同一套菜单会被重复发布（开户重跑、改渠道、换租户用同域名同 LIFF 时内容散列都一样），
    每次都盲传就会在第二次直接失败 —— 2026-09-20 线上就是这么卡住开户的。
    """
    token = frappe.conf.get("pearnly_line_channel_access_token")
    if not token:
        raise RuntimeError("LINE channel unavailable")
    response = requests.get("https://api-data.line.me/v2/bot/richmenu/"
                            + quote(menu_id, safe="") + "/content",
                            headers={"Authorization": "Bearer " + token}, timeout=20)
    return response.status_code == 200


def payload(query=False, language="th"):
    with i18n.line_language(language=language):
        actions = [
            {"type": "postback", "label": str(cards.TXT_MENU_ITEM1), "data": "action=menu_customer"},
            {"type": "postback", "label": str(cards.TXT_MENU_ITEM2), "data": "action=menu_booking"},
            account_action("portal", cards.TXT_MENU_ITEM3),
            account_action("credentials", cards.TXT_MENU_ITEM4),
        ]
        if any(action is None for action in actions):
            raise RuntimeError("Configure LIFF before publishing the DMS menus")
        if query:
            actions.append({"type": "postback", "label": str(cards.TXT_MENU_ITEM5), "data": "action=menu_query"})
        # 聊天栏上那行字：以前是欢迎语截 14 个字（泰文里显示成半句），改为一个短词。
        bar = str(cards.TXT_MENU_BAR)[:14]
    columns = 3 if query else 2
    edges = [i * 2500 // columns for i in range(columns + 1)]
    areas = []
    for index, action in enumerate(actions):
        # Rich-menu URI actions do not support Flex's desktop alternate URI.
        action = {key: value for key, value in action.items() if key != "altUri"}
        action["label"] = action["label"][:20]
        col, row = index % columns, index // columns
        areas.append({"bounds": {"x": edges[col], "y": row * 843,
                                  "width": edges[col + 1] - edges[col], "height": 843}, "action": action})
    return {"size": {"width": 2500, "height": 1686}, "selected": False,
            "chatBarText": bar, "areas": areas}


def _image(variant, language) -> bytes:
    suffix = "" if language == "th" else "-" + language
    return Path(frappe.get_app_path("pearnly_dms", "public", "images", "menu",
                                    f"rich-{variant}{suffix}.png")).read_bytes()


def publish():
    """Idempotently publish every language × variant. Never delete a previous release's menu."""
    from frappe.installer import update_site_config

    cache = _state_store()
    with cache.lock(cache.make_key("pearnly:dms:publish-menu"), timeout=120, blocking_timeout=5):
        existing = {item["name"]: item["richMenuId"] for item in _request("GET", "richmenu/list").get("richmenus", [])}
        result = {}
        for language in LANGUAGES:
            for variant in VARIANTS:
                body = payload(variant == "query", language)
                image = _image(variant, language)
                revision = hashlib.sha256(json.dumps(body, sort_keys=True).encode() + image).hexdigest()[:12]
                body["name"] = f"pearnly-dms-{language}-{variant}-{revision}"
                menu_id = existing.get(body["name"])
                if not menu_id:
                    menu_id = _request("POST", "richmenu", body=body)["richMenuId"]
                # 只有确实没有图才传（新菜单，或上一次跑到一半的补传）；已经有图就直接跳过。
                if not _has_image(menu_id):
                    _request("POST", f"richmenu/{quote(menu_id, safe='')}/content", image=image)
                result[key(language, variant)] = menu_id
        # 兼容旧键：旧代码与状态回读仍按 `basic` / `query` 取泰文那两套。
        result["basic"], result["query"] = result[key("th", "basic")], result[key("th", "query")]
        _request("POST", "user/all/richmenu/" + result["basic"])
        update_site_config(CONFIG, result)
        frappe.conf[CONFIG] = result
        reconcile()
        return result


def published_state() -> dict:
    """回读 OA 上现在真实生效的菜单：本租户发布过的两条 + 当前默认那条。

    为什么要有它：`publish()` 不报错只说明"我们发过一次请求"，不说明用户手机里
    看到的是这条菜单 —— 现场就出现过"新平台的菜单已发布、OA 默认菜单还是旧 OA 的"
    （旧菜单指着 `pearnly.com` 公共路径和另一个产品的 LIFF）。所以交付前的检查要读
    OA 的当前状态，而不是读我们自己的配置。
    """
    menus = frappe.conf.get(CONFIG) or {}
    listed = {
        item.get("name")
        for item in _request("GET", "richmenu/list").get("richmenus", [])
        if str(item.get("name") or "").startswith("pearnly-dms-")
    }
    return {
        "basic": menus.get("basic") or "",
        "query": menus.get("query") or "",
        "default": (_request("GET", "user/all/richmenu", missing_ok=True) or {}).get("richMenuId") or "",
        "listed": sorted(name for name in listed if name),
    }


def wanted_menu(menus, variant, language):
    """该挂哪一套：发布过多语言就按语言挑；只有旧的泰文两套就用那两套。"""
    return menus.get(key(language, variant)) or menus[variant]


def sync(line_user_id, language=None):
    menus = frappe.conf.get(CONFIG) or {}
    if not all(menus.get(key) for key in ("basic", "query")):
        return  # Not yet published; no external changes before the release gate.
    language = language or line.user_language(line_user_id, default="th")
    path = "user/" + quote(line_user_id, safe="") + "/richmenu"
    cache = frappe.cache()
    with cache.lock(cache.make_key("pearnly:dms:menu-user:" + line_user_id), timeout=90, blocking_timeout=3):
        for _ in range(3):
            bound = line_binding.binding_of(line_user_id)
            allowed = bool(bound and can_query({"user_id": bound.user}))
            wanted = wanted_menu(menus, "query" if allowed else "basic", language)
            if not bound:
                _request("DELETE", path, missing_ok=True)
                _remember(line_user_id, "-")
                return
            current = _request("GET", path, missing_ok=True).get("richMenuId")
            if current != wanted:
                _request("POST", path + "/" + wanted)
            frappe.db.commit()  # Next permission read must not reuse a REPEATABLE READ snapshot.
            fresh = line_binding.binding_of(line_user_id)
            if fresh and bool(can_query({"user_id": fresh.user})) == allowed:
                _remember(line_user_id, wanted)
                return
        raise RuntimeError("DMS permission changed during rich menu reconciliation")


def _remember(line_user_id, menu_id):
    try:
        frappe.cache().set_value(_APPLIED + line_user_id, menu_id, expires_in_sec=86400)
    except Exception:
        pass


def ensure(line_user_id, language):
    """每个事件顺手核一下：手机换了语言而菜单没跟上，就排一次重挂（不在 webhook 里直接写 LINE）。"""
    try:
        menus = frappe.conf.get(CONFIG) or {}
        if not menus.get(key(language, "basic")):
            return  # 还没发布多语言菜单：不动
        applied = frappe.cache().get_value(_APPLIED + line_user_id)
        # 从没挂过 → 挂一次；解绑过（"-"）不在这里挂，绑定时另有同步。
        if applied is None or (applied != "-" and applied not in {menus.get(key(language, v)) for v in VARIANTS}):
            request_sync(line_user_id)
    except Exception:
        # 菜单核对失败不能挡住这条消息本身的处理。
        pass


def request_sync(line_user_id):
    if line_user_id and frappe.conf.get(CONFIG):
        frappe.enqueue("pearnly_dms.menu.rich_menu.sync", line_user_id=line_user_id,
                       queue="short", enqueue_after_commit=True, deduplicate=True,
                       job_id="dms-menu-" + hashlib.sha256(line_user_id.encode()).hexdigest())


def operator_changed(doc, method=None):
    for user in frappe.get_all("Pearnly LINE Binding", filters={"user": doc.user}, pluck="line_user_id"):
        request_sync(user)


def reconcile():
    if not frappe.conf.get(CONFIG):
        return
    for user in frappe.get_all("Pearnly LINE Binding", pluck="line_user_id"):
        request_sync(user)
