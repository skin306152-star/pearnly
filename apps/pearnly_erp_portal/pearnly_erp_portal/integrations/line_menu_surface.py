"""ERP 富菜单随部署同步（``pearnly_core.line_surfaces`` 的约定；hooks ``pearnly_line_surfaces`` 声明本模块）。

只读对照与回退在这里，发布仍是 ``line_menu.publish()`` 本身（不改它：它是 LINE 盘点相机等真机入口的入口文件）。
菜单名与 ``publish()`` 同一算法：``payload`` + ``_image`` → 内容散列；两边一致由 tests/test_line_surfaces.py 守住
（发布后再对照必须无漂移）。图与站点无关，同一进程里记住，部署同步一次要对照两遍、ERP 每站 60 张。
"""
import hashlib
import itertools
import json

import frappe

from pearnly_core import line
from pearnly_erp_portal.integrations import line_menu as menu

SURFACE = "rich_menu"
CONFIG = menu.CONFIG
_request = menu._request
_IMAGES = {}


def ready() -> str:
    """能不能发：有渠道凭据才发（开户 ``set_line`` 同一条件）；空串 = 能。"""
    return "" if line.can_send() else "no LINE channel credentials"


def _parts(language, variant):
    body = menu.payload(variant, language)
    image = _IMAGES.get((variant, language))
    if image is None:
        image = _IMAGES[(variant, language)] = menu._image(variant, language)
    return body, image


def release(key):
    """这一版这个键（``<语言>:<组合>``）的 body 与图。"""
    language, _, variant = key.partition(":")
    return _parts(language, variant)


def _name(language, variant) -> str:
    body, image = _parts(language, variant)
    revision = hashlib.sha256(json.dumps(body, sort_keys=True).encode() + image).hexdigest()[:12]
    return f"pearnly-erp-{language}-{variant.replace('+', '-')}-{revision}"[:300]


def wanted() -> dict:
    """这一版代码每个键应挂的菜单名。"""
    return {menu.key(language, variant): _name(language, variant)
            for language, variant in itertools.product(menu.LANGUAGES, menu.VARIANTS)}


def drift() -> dict:
    """只读：站点配置里记的编号在 LINE 上对应的菜单名 ≠ 这一版应有的 → behind；配置里没有 → missing。"""
    want = wanted()
    current = dict(frappe.conf.get(CONFIG) or {})
    listed = {item["richMenuId"]: item["name"] for item in menu._request("GET", "richmenu/list").get("richmenus", [])}
    return {"behind": sorted(k for k, name in want.items() if current.get(k) and listed.get(current[k]) != name),
            "missing": sorted(k for k in want if not current.get(k))}


def publish() -> dict:
    return menu.publish()


def wants_menu(line_user_id) -> bool:
    """按当前绑定与权限这个人该不该有个人菜单（同 ``sync`` 的判据：没权限 / 解绑就摘掉）。"""
    return bool(menu.wanted_menu(line_user_id)[1])


def remount(line_user_id) -> None:
    """按当前绑定、权限与手机语言给这个人挂个人菜单（同 ``reconcile`` 排的任务，这里当场做）。"""
    menu.sync(line_user_id)


def restore(before: dict) -> None:
    """回退到部署日志里的 BEFORE 配置（旧菜单从没删过），再给已绑定用户排重挂。"""
    from frappe.installer import update_site_config

    update_site_config(CONFIG, before)
    frappe.conf[CONFIG] = before
    menu.reconcile()
