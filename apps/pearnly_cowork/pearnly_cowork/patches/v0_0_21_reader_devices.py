"""CC 桌面版多台电脑第 4 批（ADR-021，方案 4.1 第 4 步）：读文件夹的电脑。

读者从「连接」改成（电脑，成员连接）：定时任务、还没发简报的运行、公司文件夹补上 ``reader_device`` =
读者连接上那把有效密钥所在、没退役的电脑（第 1 批补丁已给每把有效密钥挂上电脑；部署前每条连接最多一把有效密钥，多于一把时
取连接当前上报的那把，再取最早的）。``reader`` 原样保留（成员连接）。这条连接上没有有效密钥的留空：之后这条连接上
报了它那个根目录的电脑来同步文件夹时接手（`folder_intake.readers.adopt`）。文件行不补：还没记电脑的文件按连接认
（`machine._mine`），在途的照常走完。

未了结的按需请求 ``target_device`` / ``account_key`` 留空：不定向，照旧谁领都行（方案 4.1 第 4 步）。

不动：任务的 ``reader``、``rule_revision``（读者没换，电脑不用重新拍底）、请求的参数。幂等：只补还空着的。
"""
import frappe

CREDENTIAL = "Cowork Express Agent Credential"
CONNECTION = "Cowork ERP Connection"
DEVICE = "Cowork CC Desktop Device"
RULE = "Cowork Folder Intake Rule"
RUN = "Cowork Folder Intake Run"
COMPANY_FOLDER = "Cowork Company Folder"
REQUEST = "Cowork Companion Request"


def _device(connection):
    """读者连接上那把有效密钥所在、没退役的电脑；没有回空串。退役不吊销密钥（`companion.devices.retire`），不排除的话
    任务会被回填到一台已移除的电脑上（一部署就标「读者已移除」，`readers.adopt` 又只接手电脑为空的，永远接不回来）。"""
    rows = frappe.db.sql(f"""SELECT k.name, k.device, c.report_credential FROM `tab{CREDENTIAL}` k
        JOIN `tab{DEVICE}` d ON d.name = k.device AND d.retired_at IS NULL
        LEFT JOIN `tab{CONNECTION}` c ON c.name = k.connection
        WHERE k.connection=%s AND k.revoked_at IS NULL AND COALESCE(k.active_key,'')<>''
        AND COALESCE(k.device,'')<>'' ORDER BY k.creation, k.name""", (connection,), as_dict=True)
    for row in rows:
        if row.name == row.report_credential:
            return row.device
    return rows[0].device if rows else ""


def execute():
    if not all(frappe.db.has_column(doctype, "reader_device") for doctype in (RULE, RUN, COMPANY_FOLDER)) \
            or not frappe.db.has_column(CREDENTIAL, "device") or not frappe.db.has_column(DEVICE, "retired_at"):
        return
    found = {}

    def device(connection):
        if connection not in found:
            found[connection] = _device(connection)
        return found[connection]

    for row in frappe.db.sql(f"""SELECT name, IFNULL(NULLIF(reader, ''), connection) AS reader FROM `tab{RULE}`
            WHERE IFNULL(reader_device, '') = ''""", as_dict=True):
        if device(row.reader):
            frappe.db.set_value(RULE, row.name, "reader_device", device(row.reader), update_modified=False)
    for row in frappe.db.sql(f"""SELECT name, connection FROM `tab{RUN}` WHERE IFNULL(reader_device, '') = ''
            AND brief_state = 'pending'""", as_dict=True):
        if device(row.connection):
            frappe.db.set_value(RUN, row.name, "reader_device", device(row.connection), update_modified=False)
    for row in frappe.db.sql(f"""SELECT name, reader FROM `tab{COMPANY_FOLDER}` WHERE IFNULL(reader_device, '') = ''""",
                             as_dict=True):
        if device(row.reader):
            frappe.db.set_value(COMPANY_FOLDER, row.name, "reader_device", device(row.reader), update_modified=False)
    if frappe.db.has_column(REQUEST, "target_device"):
        frappe.db.sql(f"""UPDATE `tab{REQUEST}` SET target_device = '' WHERE target_device IS NULL
            AND status IN ('requested', 'leased')""")
        frappe.db.sql(f"""UPDATE `tab{REQUEST}` SET account_key = '' WHERE account_key IS NULL
            AND status IN ('requested', 'leased')""")
