"""ERP 官方号的富菜单：**按岗位**给每个绑定用户挂一套（用户 2026-09-25）。

只有采购岗位的人只看到「บันทึกซื้อ」，只有销售的只看到「บันทึกขาย」，两个都有就两个都在。
图与按钮照旧线上 `pearnly-erp-v1`（旧线上 `services/line_erp/rich_menu.py`）：6 格，
左上两格是采购 / 销售，其余是「เร็ว ๆ นี้」；按钮数据仍是 `a=mode:purchase|sales`，
所以旧菜单（官方号上仍是默认菜单，未绑定的人看到它）点下去也照样走新流程。

发布是一次明确的部署动作（`publish()`，写 LINE 官方号），挂载只挂已发布的菜单；
没发布过就什么都不做，不会在半路替换官方号的菜单。
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import frappe
import requests

from pearnly_core import line_binding

CONFIG = "pearnly_erp_line_menus"
VARIANTS = ("both", "purchase", "sales")
# 旧菜单的格子：2500×1686，3 列 2 行；采购在左上，销售在中上。
_TILE = {"purchase": {"x": 0, "y": 0, "width": 833, "height": 843},
         "sales": {"x": 833, "y": 0, "width": 833, "height": 843}}
_LABELS = {"purchase": "ซื้อ", "sales": "ขาย"}


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
        raise RuntimeError(f"LINE rich menu HTTP {response.status_code}: {response.text[:200]}")
    return response.json() if response.content else {}


def payload(variant) -> dict:
    directions = ("purchase", "sales") if variant == "both" else (variant,)
    areas = [{"bounds": _TILE[d], "action": {"type": "postback", "data": "a=mode:" + d,
                                               "displayText": _LABELS[d], "label": _LABELS[d]}}
             for d in directions]
    return {"size": {"width": 2500, "height": 1686}, "selected": False,
            "chatBarText": "เมนู ERP", "areas": areas}


def _image(variant) -> bytes:
    return Path(frappe.get_app_path("pearnly_erp_portal", "public", "images", "line-menu",
                                    variant + ".png")).read_bytes()


def _has_image(menu_id) -> bool:
    token = frappe.conf.get("pearnly_line_channel_access_token")
    response = requests.get("https://api-data.line.me/v2/bot/richmenu/" + quote(menu_id, safe="")
                            + "/content", headers={"Authorization": "Bearer " + token}, timeout=20)
    return response.status_code == 200


def publish() -> dict:
    """幂等发布三套菜单（内容散列命名，重跑复用），并把已绑定用户按岗位挂上。

    不删任何旧菜单、不改官方号默认菜单（未绑定的人仍看到默认那套）。
    """
    from frappe.installer import update_site_config

    cache = frappe.cache()
    with cache.lock(cache.make_key("pearnly:erp:publish-menu"), timeout=120, blocking_timeout=5):
        existing = {item["name"]: item["richMenuId"]
                    for item in _request("GET", "richmenu/list").get("richmenus", [])}
        result = {}
        for variant in VARIANTS:
            body, image = payload(variant), _image(variant)
            revision = hashlib.sha256(json.dumps(body, sort_keys=True).encode() + image).hexdigest()[:12]
            body["name"] = f"pearnly-erp-{variant}-{revision}"
            menu_id = existing.get(body["name"]) or _request("POST", "richmenu", body=body)["richMenuId"]
            if not _has_image(menu_id):
                _request("POST", f"richmenu/{quote(menu_id, safe='')}/content", image=image)
            result[variant] = menu_id
        update_site_config(CONFIG, result)
        frappe.conf[CONFIG] = result
    reconcile()
    return result


def wanted_variant(user) -> str:
    """这个账号该看到哪套：按「LINE 开了没有」+ 采购 / 销售录入岗位。"""
    from pearnly_erp_portal.access import guard

    if not user or not guard.has("integration.read", user=user):
        return ""
    purchase, sales = guard.has("purchase.write", user=user), guard.has("sales.write", user=user)
    return "both" if purchase and sales else "purchase" if purchase else "sales" if sales else ""


def sync(line_user_id):
    menus = frappe.conf.get(CONFIG) or {}
    if not all(menus.get(key) for key in VARIANTS):
        return  # 没发布过：不碰官方号。
    path = "user/" + quote(line_user_id, safe="") + "/richmenu"
    bound = line_binding.binding_of(line_user_id)
    variant = wanted_variant(bound.user) if bound else ""
    if not variant:
        # 解绑 / 没有岗位：摘掉个人菜单，回落官方号默认菜单（点下去会提示先绑定或没有权限）。
        _request("DELETE", path, missing_ok=True)
        return
    if _request("GET", path, missing_ok=True).get("richMenuId") != menus[variant]:
        _request("POST", path + "/" + menus[variant])


def request_sync(line_user_id):
    if line_user_id and frappe.conf.get(CONFIG):
        frappe.enqueue("pearnly_erp_portal.integrations.line_menu.sync", line_user_id=line_user_id,
                       queue="short", enqueue_after_commit=True, deduplicate=True,
                       job_id="erp-menu-" + hashlib.sha256(line_user_id.encode()).hexdigest())


def user_changed(user):
    """岗位 / LINE 开关变了：这个账号名下（含刚被解绑）的 LINE 都重挂一次。"""
    for line_user_id in frappe.get_all("Pearnly LINE Binding", filters={"user": user},
                                       pluck="line_user_id"):
        request_sync(line_user_id)


def reconcile():
    for line_user_id in frappe.get_all("Pearnly LINE Binding", filters={"status": "active"},
                                       pluck="line_user_id"):
        request_sync(line_user_id)
