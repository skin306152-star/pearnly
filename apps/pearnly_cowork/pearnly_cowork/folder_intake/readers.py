"""读文件夹的是哪台电脑上的哪个成员（ADR-021 第 4 批，方案 2.7）。

读者 =（电脑，成员连接）。电脑编号归 companion（`credentials.device_id`：挂了电脑就是电脑，没挂的按密钥编号算一台），
成员连接是这台电脑同步文件夹时用的那把密钥的连接（Express 池，或只读文件夹的 MR.ERP 成员）。只按连接认，同一个池里
好几台电脑的任务会被每台各跑一遍；只按电脑认，同一台电脑上 Express 与 MR.ERP 两个成员都授权过同一个文件夹时又会跑两遍。

- 定时任务存 ``reader``（成员连接，原字段）+ ``reader_device``（电脑）；文件、运行记录、公司文件夹同样记电脑；
- 对外的编号是不透明的「电脑|连接」（`contracts.reader_ref`）。只给连接的旧写法（第 6 批之前的网页、旧对话里的参数）
  按根目录认：这条连接上哪台电脑报过这个根目录（根目录编号是那台电脑本机路径的摘要，不含电脑：几台都映射成 S 盘
  就是同一个编号）。几台都报过时只认改任务时原来那台、或绑定里带的电脑名对得上的那一台，否则拒（不猜，第 4 批审查 M2）；
- 不自动换电脑读：本机台账只在原电脑上，换读者会把拍底前的旧文件当新文件（方案 2.7）。读者被移除，任务留着原电脑，
  列表标出来，由负责人重选；
- 同一台电脑的旧槽位密钥改挂到它已认领的记录上（`companion.devices.hello`）时，这台旧记录名下的任务、在途文件、
  还没发简报的运行、公司文件夹一并转过去（钩子 ``cowork_reader_device_moved`` → :func:`moved`，companion 不导入本域）。
"""
import frappe
from frappe import _

from pearnly_cowork.companion import credentials
from pearnly_cowork.folder_intake import agent, contracts
from pearnly_cowork.folder_intake.access import COMPANY_FOLDER, FILE, RULE, RUN

def members(connection):
    """这条连接上的电脑（有一把有效密钥、没退役）：``{电脑: {"label", "online", "version", …}}``（`credentials.pool_peers`）。"""
    return credentials.pool_peers(connection) if connection else {}


def device_of(context):
    """机器请求（配对码身份）是哪台电脑。"""
    return credentials.device_id(context.credential_id)


def of(row):
    """一条任务 / 文件 / 运行 / 公司文件夹的读者：(电脑, 成员连接)。``reader`` 为空的老行就是推送去向那条连接。"""
    return str(row.get("reader_device") or ""), str(row.get("reader") or row.get("connection") or "")


def ref(row):
    return contracts.reader_ref(*of(row))


def state(device, connection):
    """这台电脑这个成员最近一次文件夹同步报的本机情况（`agent.read`）。"""
    return agent.read(connection, device or "")


def _roots(found):
    return {row.get("id") for row in found.get("roots") or [] if isinstance(row, dict)}


def resolve(value, root_id=None, *, keep=None, name=None):
    """读者编号（或只有连接的旧写法）→ (电脑, 连接)。给了电脑就要它此刻还在这条连接上；只有连接时按根目录认：
    ``keep``（改任务时原来那台）报过这个根目录就还是它；否则报过它的电脑只有一台就是那台；几台都报过（各自映射了同一个
    盘符、都有同一个本机路径）时按 ``name``（CC 桌面版绑定里带的电脑名，同步时报的那个）认出唯一一台，认不出抛
    ``root_invalid``，不按最近同步猜（猜错就读到别人电脑上的同名文件夹）。没给根目录、这条连接上只有一台电脑就是它。
    一台都没报过回 ("", 连接)：之后按根目录核时照「这台电脑没有这个根目录」拒（公司文件夹允许先绑、等电脑）。
    连接的使用授权由调用方核。"""
    try:
        device, connection = contracts.parse_reader(value)
    except contracts.ContractError as error:
        frappe.throw(_(error.code))
    found = members(connection)
    if device:
        if device not in found:
            frappe.throw(_("cowork.folder.root_invalid"))
        return device, connection
    if keep and keep in found and (root_id is None or root_id in _roots(state(keep, connection))):
        return keep, connection
    reported = []
    for candidate in sorted(found):
        current = state(candidate, connection)
        if root_id is not None and root_id in _roots(current):
            reported.append((candidate, str(current.get("device") or "")))
    if len(reported) == 1:
        return reported[0][0], connection
    named = [candidate for candidate, label in reported if name and label == name]
    if len(named) == 1:
        return named[0], connection
    if reported:
        frappe.throw(_("cowork.folder.root_invalid"))
    if root_id is None and len(found) == 1:
        return next(iter(found)), connection
    return "", connection


def adopt(connection, device, root_ids):
    """补丁时还认不出电脑（那时这条连接上没有有效密钥）的任务：这条连接上来同步文件夹、报了它那个根目录的电脑接手
    （根目录编号是本机路径的摘要，报了它就是那台的文件夹；条件更新，两台同时来也只有一台改得到）。"""
    roots = tuple(sorted({str(root) for root in root_ids or () if root}))
    if not connection or not device or not roots:
        return
    # 每次同步都会走到这里：先不加锁看一眼，绝大多数时候没有这种任务，不发更新语句
    if not frappe.db.sql(f"""SELECT name FROM `tab{RULE}` WHERE IFNULL(NULLIF(reader, ''), connection) = %s
            AND IFNULL(reader_device, '') = '' AND root_id IN %s LIMIT 1""", (connection, roots)):
        return
    frappe.db.sql(f"""UPDATE `tab{RULE}` SET reader_device = %s WHERE IFNULL(NULLIF(reader, ''), connection) = %s
        AND IFNULL(reader_device, '') = '' AND root_id IN %s""", (device, connection, roots))


def moved(connection, old, new):
    """钩子 ``cowork_reader_device_moved``：同一台电脑的旧记录 ``old`` 在这条连接上的密钥改挂到 ``new``（`companion.devices`），
    这条连接上由 ``old`` 读的任务、在途文件、还没发简报的运行、公司文件夹都转给 ``new``。调用方已锁连接与密钥行。

    ``new`` 上已经有的不重复（第 4 批审查 L4）：转过来的启用任务与 ``new`` 上某个启用任务根目录相同、文件夹相同或互相包含
    （同一台电脑上的同一个文件夹，判据同 `rules._overlapping`）就停用，不在同一台电脑上把同一批文件跑两遍、推两遍；
    这家公司在 ``new`` 上这条连接已有文件夹绑定的，``old`` 那一行删掉（留电脑现在这条记录上的），不留两行一样的让解绑永远
    拒绝。"""
    if not connection or not old or not new or old == new:
        return
    mine = frappe.db.sql(f"""SELECT root_id, folder FROM `tab{RULE}` WHERE enabled = 1 AND reader_device = %s""",
                         (new,), as_dict=True)
    if mine:
        for row in frappe.db.sql(f"""SELECT name, root_id, folder FROM `tab{RULE}` WHERE enabled = 1
                AND reader_device = %s AND IFNULL(NULLIF(reader, ''), connection) = %s""", (old, connection),
                                 as_dict=True):
            if any(other.root_id == row.root_id and contracts.nested(other.folder or "", row.folder or "")
                   for other in mine):
                frappe.db.sql(f"""UPDATE `tab{RULE}` SET enabled = 0, rule_revision = IFNULL(rule_revision, 0) + 1
                    WHERE name = %s""", (row.name,))
    taken = [row[0] for row in frappe.db.sql(f"""SELECT name FROM `tab{COMPANY_FOLDER}` WHERE reader_device = %s
        AND reader = %s AND company IN (SELECT company FROM `tab{COMPANY_FOLDER}` WHERE reader_device = %s
        AND reader = %s)""", (old, connection, new, connection))]
    if taken:
        frappe.db.delete(COMPANY_FOLDER, {"name": ("in", taken)})
    frappe.db.sql(f"""UPDATE `tab{RULE}` SET reader_device = %s WHERE reader_device = %s
        AND IFNULL(NULLIF(reader, ''), connection) = %s""", (new, old, connection))
    # 文件不论走到哪一步都转：电脑之后还要按它取结果表格（`machine._workbook`）
    frappe.db.sql(f"""UPDATE `tab{FILE}` SET reader_device = %s WHERE reader_device = %s AND connection = %s""",
                  (new, old, connection))
    frappe.db.sql(f"""UPDATE `tab{RUN}` SET reader_device = %s WHERE reader_device = %s AND connection = %s
        AND brief_state = 'pending'""", (new, old, connection))
    frappe.db.sql(f"""UPDATE `tab{COMPANY_FOLDER}` SET reader_device = %s WHERE reader_device = %s AND reader = %s""",
                  (new, old, connection))


__all__ = ["adopt", "device_of", "members", "moved", "of", "ref", "resolve", "state"]
