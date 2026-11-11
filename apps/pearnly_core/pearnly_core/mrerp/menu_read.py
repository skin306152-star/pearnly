"""读一份 MR.ERP 账套的导入菜单与主档可用值（只读、纯网络，不碰数据库；多产品共用）。

函数体原样来自 Cowork `connections/mrerp_menu_reader.py`（2026-11-03 下沉，评估 2026-11-02 §14.6）：Cowork 的读取任务与 ERP
（`pearnly_erp_portal.mrerp.choices`）调同一份。会话从共用入口拿（`mrerp.account.use`：同账号锁、被踢标记、接上缓存会话，
11-11 起不再每次新登录、把会计踢下线）；凭据由调用方交进来。``manual``（这次读取是不是人点的）由调用入口显式给。
"""
from __future__ import annotations

import logging

from pearnly_core.session_lock import SessionLockUnavailable
from pearnly_core.mrerp import account as mrerp_account
from pearnly_core.mrerp import menus as mrerp_menus
from pearnly_core.mrerp import master_reader as mrerp_master_reader
from pearnly_core.mrerp.session import SessionError

logger = logging.getLogger(__name__)

LOCK_WAIT_SECONDS = 120


def read(lease, account_set_key, *, manual):
    """→ ({导入页: 选项 | None}, {字段: 可用值}, 错误码, 主档错误码)；登录 / 选账套失败时前两项是 None。

    ``manual``：人点的（网页 / CC 的申请接口）先清被踢标记、这次照常登录；不是的有标记就不发请求，回 ``ERR_ACCOUNT_IN_USE``。"""
    comidyear, _, seldb = str(account_set_key or "").partition(":")
    if not lease.get("username") or not lease.get("password"):
        return None, None, "ERR_NO_CREDS", None
    try:
        with mrerp_account.use(lease["system_url"], lease["username"], lease["password"], manual=manual,
                               lock_wait=LOCK_WAIT_SECONDS) as session:
            first = next(iter(mrerp_menus.PAGES.items()))
            session.select(comidyear, seldb, probe=f"{first[0]}/formupload.php?idmenu={first[1]}")
            pages = {}
            for path, idmenu in mrerp_menus.PAGES.items():
                page = session.request("GET", f"{path}/formupload.php?idmenu={idmenu}",
                                       headers={"Referer": f"{session.base}/login/mainmenu.php"})
                pages[path] = mrerp_menus.parse_options(page.text or "")
            fields, master_error = mrerp_master_reader.read_for_choices(session.request, session.base)
            return pages, fields, None, master_error
    except SessionLockUnavailable:
        return None, None, "ERR_TECHNICAL", None
    except SessionError as exc:
        logger.info("MR.ERP menu read failed: %s", exc)
        return None, None, exc.code, None
    except Exception:  # noqa: BLE001 — 契约：读取失败只落错误码
        logger.exception("MR.ERP menu read raised")
        return None, None, "ERR_UNEXPECTED", None
