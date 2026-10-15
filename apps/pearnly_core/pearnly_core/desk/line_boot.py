"""本站点的 LIFF ID 进 Desk 启动信息：盘点扫码在 LINE 普通内置浏览器里打不开相机时，转到 LIFF 打开同一页，
由 LINE 弹原生的相机授权（2026-10-15 三星 A14 实测：普通内置浏览器不弹也不报错，LIFF 会弹）。ID 不是密钥。"""
import frappe


def boot_session(boot):
    # boot 失败会让整个 Desk 打不开：这里绝不往外抛（与站点货币那份同一口径）。
    try:
        from pearnly_core import line

        boot.pearnly_liff_id = line.liff_id()
    except Exception:
        frappe.log_error(title="pearnly_core.desk.line_boot.boot_session 失败")
