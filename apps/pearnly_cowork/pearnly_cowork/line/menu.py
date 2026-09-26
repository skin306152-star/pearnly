"""Cowork 官方号的富菜单：**按权限、按手机语言**给每个绑定用户挂一套（用户 2026-09-26：照 ERP）。

照 ERP `pearnly_erp_portal.integrations.line_menu` 的做法（产品各自一份，不改 ERP / 共用层），
版式照旧线上 `pearnly-cowork-work-v3`（3 列 2 行）：左上「发单据进 ERP」（postback
`action=cowork_erp_start`，与旧菜单同一个，旧菜单点下去也走新流程）、中上「库存盘点」（在 LINE 内置
浏览器打开网页端原生盘点页）、右上「工作协调」（同样只是打开网页端看板，用户 2026-09-27："line 只做点击
然后进入这个界面，不做多余功能"）；没有那项权限的格子只画锁、不可点。其余格子一直是锁
（ERP 09-26 口径：锁上不写「เร็ว ๆ นี้」）。

图由 `scripts/render-cowork-line-menu-tiles.py` 预先画好的格子在发布时拼成（产品镜像里不需要字体）。
发布是一次明确的部署动作（`publish()`，写 LINE 官方号）；没发布过就什么都不做，不替换官方号的菜单。
"""
import hashlib
import io
import itertools
import json
from pathlib import Path
from urllib.parse import quote

import frappe
import requests
from frappe import _

from pearnly_core import line, line_binding

from pearnly_cowork.access import scope
from pearnly_cowork.line import binding, flow

CONFIG = "pearnly_cowork_line_menus"
# 格子顺序即位置（3 列 2 行，左上起）；其余格子恒为锁。
FUNCTIONS = ("intake", "stocktake", "work")
VARIANTS = tuple("+".join(combo) for size in range(1, len(FUNCTIONS) + 1)
                 for combo in itertools.combinations(FUNCTIONS, size))
LANGUAGES = line.USER_LANGUAGES
SIZE, CELL = (2500, 1686), (833, 843)
BACKGROUND = "#F8F9FD"
STOCKTAKE_ROUTE = "/desk/pearnly-stocktake"
WORK_ROUTE = "/desk/pearnly-work"
_APPLIED = "pearnly:cowork:line-menu:"


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
        raise RuntimeError(f"LINE rich menu HTTP {response.status_code}: {response.text[:200]}")
    return response.json() if response.content else {}


def _cell(index):
    column, row = index % 3, index // 3
    return {"x": column * CELL[0], "y": row * CELL[1],
            "width": SIZE[0] - column * CELL[0] if column == 2 else CELL[0], "height": CELL[1]}


def payload(variant, language) -> dict:
    present = variant.split("+")
    areas = []
    for index, name in enumerate(FUNCTIONS):
        if name not in present:
            continue
        if name == "stocktake":
            action = {"type": "uri", "label": _("Stock count", lang=language)[:20],
                      "uri": frappe.utils.get_url().rstrip("/") + STOCKTAKE_ROUTE}
        elif name == "work":
            action = {"type": "uri", "label": _("Work Coordination", lang=language)[:20],
                      "uri": frappe.utils.get_url().rstrip("/") + WORK_ROUTE}
        else:
            label = _("Send documents to ERP", lang=language)
            action = {"type": "postback", "data": flow.START, "displayText": label, "label": label[:20]}
        areas.append({"bounds": _cell(index), "action": action})
    return {"size": {"width": SIZE[0], "height": SIZE[1]}, "selected": False,
            "chatBarText": _("Cowork menu", lang=language)[:14], "areas": areas}


def _image(variant, language) -> bytes:
    """把预先画好的格子拼成一张菜单（无字体依赖；同样的格子拼出同样的字节）。"""
    from PIL import Image

    tiles = Path(frappe.get_app_path("pearnly_cowork", "public", "images", "line-menu", "tiles"))
    present = variant.split("+")
    canvas = Image.new("RGB", SIZE, BACKGROUND)
    for index in range(6):
        name = FUNCTIONS[index] if index < len(FUNCTIONS) else None
        tile = f"{name}-{language}.png" if name in present else "locked.png"
        cell = _cell(index)
        canvas.paste(Image.open(tiles / tile).convert("RGB"), (cell["x"], cell["y"]))
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


def _has_image(menu_id) -> bool:
    token = frappe.conf.get("pearnly_line_channel_access_token")
    response = requests.get("https://api-data.line.me/v2/bot/richmenu/" + quote(menu_id, safe="")
                            + "/content", headers={"Authorization": "Bearer " + token}, timeout=20)
    return response.status_code == 200


def publish() -> dict:
    """幂等发布「语言 × 权限组合」的菜单（内容散列命名，重跑复用），并把已绑定用户挂上。

    不删任何旧菜单、不改官方号默认菜单（未绑定的人仍看到默认那套）。
    """
    from frappe.installer import update_site_config

    cache = frappe.cache()
    with cache.lock(cache.make_key("pearnly:cowork:publish-menu"), timeout=120, blocking_timeout=5):
        existing = {item["name"]: item["richMenuId"]
                    for item in _request("GET", "richmenu/list").get("richmenus", [])}
        result = {}
        for language, variant in itertools.product(LANGUAGES, VARIANTS):
            body, image = payload(variant, language), _image(variant, language)
            revision = hashlib.sha256(json.dumps(body, sort_keys=True).encode() + image).hexdigest()[:12]
            body["name"] = f"pearnly-cowork-{language}-{variant.replace('+', '-')}-{revision}"[:300]
            menu_id = existing.get(body["name"]) or _request("POST", "richmenu", body=body)["richMenuId"]
            if not _has_image(menu_id):
                _request("POST", f"richmenu/{quote(menu_id, safe='')}/content", image=image)
            result[key(language, variant)] = menu_id
        update_site_config(CONFIG, result)
        frappe.conf[CONFIG] = result
    reconcile()
    return result


def wanted_variant(user) -> str:
    """这个账号该看到哪套：LINE 开了没有 + 录入 / 盘点 / 工作协调权限（与各自页面同一判据）。"""
    from pearnly_cowork.stocktake import access as stocktake
    from pearnly_cowork.work import access as work

    if not user or not binding.enabled(user):
        return ""
    has = {"intake": scope.allowed("intake", user), "stocktake": stocktake.allowed(user),
           "work": work.allowed(user)}
    return "+".join(name for name in FUNCTIONS if has[name])


def _published():
    menus = frappe.conf.get(CONFIG) or {}
    return menus if all(menus.get(key(language, variant)) for language in LANGUAGES
                        for variant in VARIANTS) else None


def wanted_menu(line_user_id, language=None):
    menus = _published()
    bound = line_binding.binding_of(line_user_id) if menus else None
    variant = wanted_variant(bound.user) if bound else ""
    if not variant:
        return menus, ""
    return menus, menus[key(language or line.user_language(line_user_id), variant)]


def sync(line_user_id):
    menus, wanted = wanted_menu(line_user_id)
    if not menus:
        return  # 没发布过：不碰官方号。
    path = "user/" + quote(line_user_id, safe="") + "/richmenu"
    if not wanted:
        # 解绑 / 没有权限：摘掉个人菜单，回落官方号默认菜单（点下去会提示先绑定或没有权限）。
        _request("DELETE", path, missing_ok=True)
    elif _request("GET", path, missing_ok=True).get("richMenuId") != wanted:
        _request("POST", path + "/" + wanted)
    try:
        frappe.cache.set_value(_APPLIED + line_user_id, wanted or "-", expires_in_sec=86400)
    except Exception:
        pass


def ensure(line_user_id, language):
    """每个事件顺手核一下：手机换了语言、权限变了而菜单没跟上，就排一次重挂（不直接写 LINE）。"""
    try:
        menus, wanted = wanted_menu(line_user_id, language)
        if menus and frappe.cache.get_value(_APPLIED + line_user_id) != (wanted or "-"):
            request_sync(line_user_id)
    except Exception:
        # 菜单核对失败不能挡住这条消息本身的处理；记下来就好。
        try:
            frappe.log_error(title="Cowork LINE menu check failed", message=frappe.get_traceback()[-1500:])
        except Exception:
            pass


def request_sync(line_user_id):
    if line_user_id and frappe.conf.get(CONFIG):
        frappe.enqueue("pearnly_cowork.line.menu.sync", line_user_id=line_user_id,
                       queue="short", enqueue_after_commit=True, deduplicate=True,
                       job_id="cowork-menu-" + hashlib.sha256(line_user_id.encode()).hexdigest())


def reconcile():
    for line_user_id in frappe.get_all("Pearnly LINE Binding", filters={"status": "active"},
                                       pluck="line_user_id"):
        request_sync(line_user_id)
