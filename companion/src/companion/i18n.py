# -*- coding: utf-8 -*-
"""客户可见文案的双语层(泰语 TH + 中文 ZH)· 配对窗 + 托盘 + 提示/报错统一走 t(key,lang)。

中文为权威文案(专业 · 非口语);泰语为地道专业泰语(客户语言)。默认按 Windows 语言
(泰→th / 中→zh / 其它→th),可在配对窗右上角切换并存进 config.ui_lang 记住。
红线:不出现甩锅/口语(「是你的责任」「自动干活」「选你自家那个」等)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

LANGS = ("th", "zh")
_DEFAULT = "th"

# key: (zh, th)。带 {n}/{company}/{err} 占位的用 t(..., n=) 格式化。
_M = {
    "lang_toggle": ("中文 / ไทย", "中文 / ไทย"),
    "win_title_add": ("添加成员", "เพิ่มสมาชิก"),
    "win_title_edit": ("更换配对码", "เปลี่ยนรหัสจับคู่"),
    "already_running_title": ("Pearnly 小助手", "ผู้ช่วย Pearnly"),
    "already_running_body": (
        "Pearnly 小助手已在运行，请在系统托盘（任务栏右下角）中查看。",
        "ผู้ช่วย Pearnly กำลังทำงานอยู่แล้ว กรุณาตรวจสอบที่ถาดระบบ (มุมขวาล่างของแถบงาน)",
    ),
    "lbl_code": ("配对码", "รหัสจับคู่"),
    "ph_code": (
        "在 Pearnly 网页「连接 Express」中获取",
        "รับได้จากหน้า「เชื่อมต่อ Express」บนเว็บ Pearnly",
    ),
    "status_scanning": ("正在查找…", "กำลังค้นหา…"),
    # 主窗口「这台电脑」：Express 账套是全局设置（检测到的数据根全部授权，网页再选年度 / 账套）。
    "sec_machine": ("本机", "เครื่องนี้"),
    "lbl_express": ("Express 账套", "ชุดบัญชี Express"),
    "sec_members": ("成员", "สมาชิก"),
    "express_roots_more": ("{path} 等 {n} 个年度", "{path} รวม {n} ปีบัญชี"),
    "status_reg_error": (
        "无法读取 Express 公司列表，请确认 Express 路径。",
        "ไม่สามารถอ่านรายชื่อบริษัทจาก Express ได้ กรุณาตรวจสอบตำแหน่งโฟลเดอร์ Express",
    ),
    "btn_browse": ("更换…", "เปลี่ยน…"),
    "browse_title": (
        "请选择 Express 数据所在文件夹",
        "กรุณาเลือกโฟลเดอร์ข้อมูล Express",
    ),
    "browse_hint": (
        "未找到 Express，请点击「更换…」选择 Express 数据所在文件夹。",
        "ไม่พบ Express กรุณากด «เปลี่ยน…» แล้วเลือกโฟลเดอร์ข้อมูล Express",
    ),
    "chk_autostart": ("开机时自动启动小助手", "เริ่มผู้ช่วยอัตโนมัติเมื่อเปิดเครื่อง"),
    "btn_cancel": ("取消", "ยกเลิก"),
    "btn_pair": ("配对", "จับคู่"),
    "btn_save": ("保存", "บันทึก"),
    "pairing": ("正在校验配对码…", "กำลังตรวจสอบรหัสจับคู่…"),
    "pair_fail": ("配对失败：{err}", "จับคู่ไม่สำเร็จ: {err}"),
    # pairing.pair() 内部错误(按 lang 本地化,供配对窗/单测统一走 t())
    "pair_err_token_format": (
        "配对码不完整，请在网页连接窗口点击「复制」后完整粘贴。",
        "รหัสจับคู่ไม่ครบถ้วน กรุณากด「คัดลอก」ในหน้าต่างเชื่อมต่อบนเว็บ แล้ววางรหัสทั้งหมด",
    ),
    "pair_err_code_required": (
        "请填写配对码。",
        "กรุณากรอกรหัสจับคู่",
    ),
    "pair_err_no_chosen": ("请从列表中选择账套", "กรุณาเลือกชุดบัญชีจากรายการ"),
    "pair_err_dir_missing": (
        "所选账套目录不存在：{dir}",
        "ไม่พบโฟลเดอร์ของชุดบัญชีที่เลือก: {dir}",
    ),
    "pair_err_dir_empty": (
        "未提供账套目录。",
        "ไม่ได้ระบุโฟลเดอร์ชุดบัญชี",
    ),
    # 账套数据在某盘符(如 S:\…),但本机没有该盘 → 指路:装到做账那台 / 映射该盘。
    "pair_err_drive_missing": (
        "该账套数据位于「{drive}」盘，本机不存在该驱动器。\n请在使用 Express 录入此账套的电脑（有「{drive}」盘）上安装小助手，或在本机将「{drive}」盘映射到相同位置。",
        "ข้อมูลของชุดบัญชีนี้อยู่ในไดรฟ์ «{drive}» แต่เครื่องนี้ไม่มีไดรฟ์ดังกล่าว\nกรุณาติดตั้งผู้ช่วย Pearnly บนเครื่องที่ใช้ Express บันทึกชุดบัญชีนี้ (เครื่องที่มีไดรฟ์ «{drive}») หรือแมปไดรฟ์ «{drive}» บนเครื่องนี้ไปยังตำแหน่งเดียวกัน",
    ),
    "pair_err_probe_fail": (
        "未能读取 Express 账套,请检查所选文件夹。",
        "ไม่สามารถอ่านชุดบัญชี Express ได้ กรุณาตรวจสอบโฟลเดอร์ที่เลือก",
    ),
    "pair_err_invalid_code": (
        "配对码无效",
        "รหัสจับคู่ไม่ถูกต้อง",
    ),
    "pair_err_disabled": (
        "此连接已停用，请联系管理员。",
        "การเชื่อมต่อนี้ถูกปิดใช้งานแล้ว กรุณาติดต่อผู้ดูแลระบบ",
    ),
    "pair_err_push_off": (
        "云端推送未启用，请联系管理员。",
        "ยังไม่ได้เปิดใช้การส่งข้อมูลบนคลาวด์ กรุณาติดต่อผู้ดูแลระบบ",
    ),
    "pair_err_http": (
        "连接云端失败（HTTP {code}）",
        "เชื่อมต่อคลาวด์ไม่สำเร็จ (HTTP {code})",
    ),
    "pair_err_network": (
        "无法连接 Pearnly，请检查网络后重试。",
        "ไม่สามารถเชื่อมต่อ Pearnly ได้ กรุณาตรวจสอบเครือข่ายแล้วลองอีกครั้ง",
    ),
    "pair_err_unexpected": (
        "操作未完成,请重试。",
        "ดำเนินการไม่สำเร็จ กรุณาลองใหม่",
    ),
    "pair_err_rejected": ("云端校验未通过", "การตรวจสอบจากคลาวด์ไม่ผ่าน"),
    "pair_err_profile_save": (
        "无法保存连接，请重试。",
        "ไม่สามารถบันทึกการเชื่อมต่อได้ กรุณาลองใหม่",
    ),
    # 多账套管理
    # 主窗口（2026-10-08 起连接、文件夹、开机启动都在这一个窗口里；托盘左键或「打开小助手」打开）。
    "profiles_title": ("Pearnly 小助手", "ผู้ช่วย Pearnly"),
    "profiles_empty": (
        "暂无成员。请在网页「连接 Express」中复制配对码，然后点击「添加成员」。",
        "ยังไม่มีสมาชิก กรุณาคัดลอกรหัสจับคู่จากหน้า «เชื่อมต่อ Express» บนเว็บ แล้วกด «เพิ่มสมาชิก»",
    ),
    "profiles_active": ("已启用", "ใช้งานอยู่"),
    "profiles_paused": ("已暂停", "พักการใช้งาน"),
    "profiles_connecting": ("正在连接 Pearnly", "กำลังเชื่อมต่อ Pearnly"),
    "profiles_online": ("Pearnly 已连接", "เชื่อมต่อ Pearnly แล้ว"),
    "profiles_processing": ("处理中", "กำลังประมวลผล"),
    "profiles_attention": ("需处理", "ต้องดำเนินการ"),
    "profiles_offline": ("Pearnly 连接中断", "ขาดการเชื่อมต่อกับ Pearnly"),
    "profiles_add": ("添加成员", "เพิ่มสมาชิก"),
    "profiles_edit": ("更换配对码…", "เปลี่ยนรหัสจับคู่…"),
    "profiles_pause": ("暂停", "พักการใช้งาน"),
    "profiles_resume": ("恢复", "กลับมาใช้งาน"),
    "profiles_remove": ("移除", "นำออก"),
    "profiles_remove_title": ("移除成员", "นำสมาชิกออก"),
    "profiles_remove_body": (
        "确定要移除成员「{company}」吗？Express 中的数据不会被删除。",
        "ต้องการนำสมาชิก «{company}» ออกใช่หรือไม่ ข้อมูลใน Express จะไม่ถูกลบ",
    ),
    "profiles_error": (
        "无法更新连接，请重试。",
        "ไม่สามารถอัปเดตการเชื่อมต่อได้ กรุณาลองใหม่",
    ),
    # 托盘
    "tray_tooltip": ("Pearnly 小助手", "ผู้ช่วย Pearnly"),
    "tray_starting": ("启动中…", "กำลังเริ่ม…"),
    "tray_open_logs": ("打开日志文件夹", "เปิดโฟลเดอร์ไฟล์บันทึกการทำงาน"),
    # 文件夹自动录入：本机只设网页可选的根目录（安全边界），其余在网页「自动录入规则」里设。
    "folders_title": ("自动录入文件夹", "โฟลเดอร์บันทึกอัตโนมัติ"),
    "folders_label": ("网页可选的文件夹", "โฟลเดอร์ที่เลือกได้บนเว็บ"),
    "folders_add": ("添加文件夹…", "เพิ่มโฟลเดอร์…"),
    "folders_remove": ("移除", "นำออก"),
    "folders_save": ("保存", "บันทึก"),
    "folders_pick": ("选择允许读取的文件夹", "เลือกโฟลเดอร์ที่อนุญาตให้อ่าน"),
    "folders_overlap": (
        "该文件夹已被其他连接使用或与其存在包含关系：{path}",
        "โฟลเดอร์นี้ถูกใช้กับการเชื่อมต่ออื่นแล้ว หรือซ้อนทับกับโฟลเดอร์ของการเชื่อมต่ออื่น: {path}",
    ),
    "log_folder_run": ("开始自动录入：{name}", "เริ่มบันทึกอัตโนมัติ: {name}"),
    "log_folder_done": (
        "自动录入已上传：{name} · 新文件 {n} 份，失败 {fail} 份",
        "บันทึกอัตโนมัติ อัปโหลดแล้ว: {name} · ไฟล์ใหม่ {n} ไฟล์ ไม่สำเร็จ {fail} ไฟล์",
    ),
    "tray_quit": ("退出", "ออกจากโปรแกรม"),
    "st_idle": ("空闲", "พร้อมทำงาน"),
    "st_working": ("正在录入", "กำลังบันทึก"),
    "st_indexing": (
        "正在更新索引…",
        "กำลังอัปเดตดัชนี…",
    ),
    "st_waiting_lock": (
        "正在等待 Express 释放账套…",
        "กำลังรอให้ Express ปล่อยชุดบัญชี…",
    ),
    # 账套被占用弹窗(写盘前探测到独占拿不到 → 原生弹窗交用户:重试 / 终止整批)
    "busy_title": ("账套正被占用", "ชุดบัญชีกำลังถูกใช้งาน"),
    "busy_body": (
        "Express 账套正被占用（可能在其他电脑或服务器上打开），暂时无法录入。\n请关闭正在使用该账套的 Express 后点击「重试」，或点击「终止推送」放弃本批。",
        "ชุดบัญชี Express กำลังถูกใช้งานอยู่ (อาจเปิดอยู่บนเครื่องอื่นหรือบนเซิร์ฟเวอร์) จึงยังไม่สามารถบันทึกได้ในขณะนี้\nกรุณาปิด Express ที่ใช้ชุดบัญชีนี้อยู่ แล้วกด「ลองใหม่」 หรือกด「ยกเลิกการส่ง」เพื่อยกเลิกชุดนี้",
    ),
    "busy_retry": ("重试", "ลองใหม่"),
    "busy_abort": ("终止推送", "ยกเลิกการส่ง"),
    "log_busy_aborted": (
        "账套被占用，已终止本批推送。",
        "ชุดบัญชีถูกใช้งานอยู่ ยกเลิกการส่งชุดนี้แล้ว",
    ),
    "st_error": ("出错", "เกิดข้อผิดพลาด"),
    "st_offline": ("离线", "ออฟไลน์"),
    "st_connected": ("已连接", "เชื่อมต่อแล้ว"),
    "st_stalled_giveup": (
        "连接异常，请检查账套路径或网络后重新启动小助手",
        "การเชื่อมต่อผิดปกติ กรุณาตรวจสอบตำแหน่งชุดบัญชีหรือเครือข่าย แล้วเริ่มผู้ช่วยใหม่",
    ),
    # 托盘只留「打开小助手」一个入口（连接、文件夹、开机启动都在主窗口）。
    "tray_open": ("打开小助手", "เปิดผู้ช่วย"),
    # 离线友好原因(替代英文异常类名 HTTPStatusError):
    "off_auth": ("配对码已失效，请重新配对", "รหัสจับคู่ใช้งานไม่ได้แล้ว กรุณาจับคู่ใหม่"),
    "off_disabled": ("此连接已被停用", "การเชื่อมต่อนี้ถูกปิดใช้งานแล้ว"),
    "off_push_off": ("云端推送未开启", "ยังไม่ได้เปิดการส่งข้อมูลบนคลาวด์"),
    "off_network": (
        "无法连接 Pearnly，请检查网络",
        "ไม่สามารถเชื่อมต่อ Pearnly ได้ กรุณาตรวจสอบเครือข่าย",
    ),
    # 活动记录(activity.log · 客户「打开日志」看的人话事件):
    "log_started": (
        "小助手已启动。",
        "ผู้ช่วยเริ่มทำงานแล้ว",
    ),
    "log_connected": (
        "已连接 Pearnly，等待新发票。",
        "เชื่อมต่อ Pearnly แล้ว กำลังรอใบกำกับภาษีใหม่",
    ),
    "log_offline": ("与 Pearnly 的连接已断开：{reason}", "ขาดการเชื่อมต่อกับ Pearnly: {reason}"),
    "log_self_healed": (
        "小助手已自动重启。",
        "ผู้ช่วยรีสตาร์ทอัตโนมัติแล้ว",
    ),
    "log_recorded": ("已录入 1 张发票（{inv}）。", "บันทึกใบกำกับภาษีแล้ว 1 ใบ ({inv})"),
    "log_record_fail": ("发票录入失败：{err}", "บันทึกใบกำกับภาษีไม่สำเร็จ: {err}"),
    "log_paired": ("配对成功，账套：{company}。", "จับคู่สำเร็จ ชุดบัญชี: {company}"),
    "log_quit": ("小助手已退出。", "ผู้ช่วยปิดการทำงานแล้ว"),
    # 自动更新
    "tray_update_to": ("更新到最新版 v{ver}", "อัปเดตเป็นเวอร์ชันล่าสุด v{ver}"),
    # 录入并重建索引完成(每张写盘后即重建索引 · 真可查)→ 系统原生气泡
    "notif_recorded_title": ("已录入 Express", "บันทึกเข้า Express แล้ว"),
    "notif_recorded_one": (
        "{inv} 已录入 Express。",
        "{inv} บันทึกเข้า Express แล้ว",
    ),
    "notif_recorded_many": (
        "本批 {n} 张已录入 Express。",
        "บันทึกเข้า Express แล้ว {n} ใบในชุดนี้",
    ),
    # 本批有失败 → 同样弹原生通知(诚实反馈·不拿失败冒充成功)
    "notif_partial_title": ("本批部分录入", "ชุดนี้บันทึกได้บางส่วน"),
    "notif_partial_body": (
        "本批 {ok} 张已录入，{fail} 张录入失败，请在网页中处理。",
        "ชุดนี้บันทึกแล้ว {ok} ใบ ไม่สำเร็จ {fail} ใบ กรุณาดำเนินการบนเว็บ",
    ),
    "notif_failed_title": ("本批录入失败", "ชุดนี้บันทึกไม่สำเร็จ"),
    "notif_failed_body": (
        "本批 {fail} 张录入失败，请在网页日志中查看原因。",
        "ชุดนี้บันทึกไม่สำเร็จ {fail} ใบ กรุณาดูสาเหตุในบันทึกการทำงานบนเว็บ",
    ),
    "update_avail_title": ("有新版本", "มีเวอร์ชันใหม่"),
    "update_avail_body": (
        "Pearnly 小助手 v{ver} 已发布，当前录入完成后将自动更新并重启。",
        "ผู้ช่วย Pearnly v{ver} พร้อมใช้งานแล้ว ระบบจะอัปเดตและรีสตาร์ทอัตโนมัติเมื่อบันทึกรายการปัจจุบันเสร็จ",
    ),
    "update_downloading": (
        "正在下载更新，完成后将自动重启…",
        "กำลังดาวน์โหลดอัปเดต ระบบจะรีสตาร์ทอัตโนมัติเมื่อเสร็จ…",
    ),
    "update_failed": (
        "更新失败，请稍后重试或从网页重新下载安装。",
        "อัปเดตไม่สำเร็จ กรุณาลองใหม่ภายหลัง หรือดาวน์โหลดและติดตั้งใหม่จากเว็บ",
    ),
    # 主窗口底部的更新按钮与状态（2026-10-08：发布后一分钟内发现新版，按钮可立即检查 / 立即更新）。
    "upd_check": ("检查更新", "ตรวจสอบอัปเดต"),
    "upd_now": ("立即更新", "อัปเดตทันที"),
    "upd_checking": ("正在检查…", "กำลังตรวจสอบ…"),
    "upd_latest": ("已是最新版本", "เป็นเวอร์ชันล่าสุดแล้ว"),
    "upd_found": ("发现新版本 v{ver}", "พบเวอร์ชันใหม่ v{ver}"),
    "upd_downloading": ("正在下载 v{ver}…", "กำลังดาวน์โหลด v{ver}…"),
    "upd_ready": ("v{ver} 已下载，当前录入完成后将自动安装", "ดาวน์โหลด v{ver} แล้ว จะติดตั้งอัตโนมัติเมื่อบันทึกรายการปัจจุบันเสร็จ"),
    "upd_installing": ("正在安装 v{ver}，小助手将自动重启", "กำลังติดตั้ง v{ver} ผู้ช่วยจะรีสตาร์ทอัตโนมัติ"),
    "upd_failed": ("v{ver} 下载失败，稍后将自动重试", "ดาวน์โหลด v{ver} ไม่สำเร็จ ระบบจะลองใหม่อัตโนมัติภายหลัง"),
    "log_update_found": ("发现新版本 v{ver}。", "พบเวอร์ชันใหม่ v{ver}"),
    "log_updating": ("正在更新到 v{ver}…", "กำลังอัปเดตเป็น v{ver}…"),
}


@dataclass(frozen=True)
class MessageSpec:
    """可随界面语言重新渲染的客户文案。

    fmt 值也可为 MessageSpec，供「配对失败:{具体原因}」这类嵌套文案在切换语言后
    整体重译；异常对象不得放进 fmt，原始异常只写技术日志。
    """

    key: str
    fmt: Mapping[str, Any] = field(default_factory=dict)

    def render(self, lang: str) -> str:
        values = {
            name: value.render(lang) if isinstance(value, MessageSpec) else value
            for name, value in self.fmt.items()
        }
        return t(self.key, lang, **values)


@dataclass(frozen=True)
class StatusSpec:
    message: MessageSpec
    ok: bool = True


def persist_ui_lang(store, lang: str) -> str:
    """只更新 schema v2 的 global.ui_lang，不改任何 Express Profile。"""
    selected = normalize(lang)
    cfg = store.new_runtime_config()
    cfg.update(ui_lang=selected)
    store.save_profile(cfg)
    return selected


def default_lang() -> str:
    """按 Windows 用户界面语言:泰→th / 中→zh / 其它→th(客户多为泰语)。"""
    try:
        import ctypes

        primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        if primary == 0x04:  # 中文
            return "zh"
        if primary == 0x1E:  # 泰语
            return "th"
    except Exception:
        pass
    return _DEFAULT


def normalize(lang: str) -> str:
    lang = (lang or "").strip().lower()
    return lang if lang in LANGS else default_lang()


def t(key: str, lang: str, **fmt) -> str:
    """取 key 的 lang 文案(缺 lang 用默认);带占位则按 fmt 格式化。"""
    pair = _M.get(key)
    if not pair:
        return key
    s = pair[1] if normalize(lang) == "th" else pair[0]
    return s.format(**fmt) if fmt else s


def http_status_category(code: int) -> str:
    """HTTP 状态码 → 语义分类(供离线轮询 gui_tray._friendly_poll_error 与
    pairing.pair() 两处报错文案共用,避免各自维护一份 401/403/404 分支)。"""
    if code == 401:
        return "auth"
    if code == 403:
        return "disabled"
    if code == 404:
        return "push_off"
    return "other"
