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
    "win_title": ("Pearnly 小助手 · 首次设置", "Pearnly ผู้ช่วย · ตั้งค่าครั้งแรก"),
    "win_title_add": (
        "Pearnly 小助手 · 添加 Express 连接",
        "Pearnly ผู้ช่วย · เพิ่มการเชื่อมต่อ Express",
    ),
    "win_title_edit": (
        "Pearnly 小助手 · 编辑 Express 连接",
        "Pearnly ผู้ช่วย · แก้ไขการเชื่อมต่อ Express",
    ),
    "already_running_title": ("Pearnly 小助手", "Pearnly ผู้ช่วย"),
    "already_running_body": (
        "Pearnly 小助手已在运行,请在系统托盘查看(任务栏右下角的图标)。",
        "Pearnly ผู้ช่วยกำลังทำงานอยู่แล้ว ดูได้ที่ถาดระบบ (ไอคอนมุมขวาล่างของแถบงาน)",
    ),
    "intro": (
        "Pearnly 小助手会将识别后的发票自动录入本地 Express。完成以下设置后,小助手将在后台运行,全程无需手动操作 Express。",
        "Pearnly ผู้ช่วยจะบันทึกใบกำกับภาษีที่อ่านค่าแล้วเข้าสู่ Express ในเครื่องโดยอัตโนมัติ "
        "เมื่อตั้งค่าเสร็จแล้ว ผู้ช่วยจะทำงานเบื้องหลังให้เอง โดยไม่ต้องเปิด Express ด้วยตนเอง",
    ),
    "sec_connect": ("连接 Pearnly", "เชื่อมต่อ Pearnly"),
    "sec_account": ("选择 Express 账套", "เลือกชุดบัญชี Express"),
    "lbl_code": ("配对码", "รหัสจับคู่"),
    "ph_code": (
        "在 Pearnly 网页「连接 Express」中获取",
        "รับได้จากหน้า「เชื่อมต่อ Express」บนเว็บ Pearnly",
    ),
    "lbl_root": ("年度 / 数据根", "ปี / ชุดข้อมูล Express"),
    "ph_root": ("选择 Express 年度数据根", "เลือกปีข้อมูล Express"),
    "ph_root_scanning": ("正在检测…", "กำลังค้นหา…"),
    "status_scanning": (
        "正在检测 Express 数据(可能需几秒)…",
        "กำลังค้นหาข้อมูล Express (อาจใช้เวลาสักครู่)…",
    ),
    "root_item": ("{name} · {n} 家", "{name} · {n} บริษัท"),
    "lbl_acct": ("选择账套", "เลือกชุดข้อมูลบัญชี"),
    "ph_acct_loading": ("正在加载账套列表…", "กำลังโหลดรายการชุดข้อมูล…"),
    "ph_acct_select": ("请选择账套", "กรุณาเลือกชุดข้อมูล"),
    "help_acct": (
        "请选择默认账套。提交每批单据时还可在网页或 LINE 改选已授权的其他年度与账套。",
        "กรุณาเลือกชุดข้อมูลบัญชีของบริษัทท่าน เอกสารจะถูกบันทึกเข้าสู่ชุดข้อมูลที่ท่านเลือกอย่างถูกต้อง "
        "และสามารถเปลี่ยนปีหรือชุดบัญชีที่ได้รับอนุญาตได้ก่อนส่งแต่ละครั้งบนเว็บหรือ LINE",
    ),
    "chk_all_roots": (
        "允许此连接在网页和 LINE 中选择已检测到的全部年度与账套",
        "อนุญาตให้การเชื่อมต่อนี้เลือกทุกปีและทุกชุดบัญชีที่ตรวจพบจากเว็บและ LINE",
    ),
    "status_found": (
        "已找到 Express 数据。",
        "พบข้อมูล Express แล้ว",
    ),
    "status_reg_error": (
        "未能读取 Express 公司列表,请确认 Express 路径。",
        "ไม่สามารถอ่านรายชื่อบริษัทจาก Express ได้ กรุณาตรวจสอบที่อยู่ของ Express",
    ),
    "btn_browse": ("选择 / 更换 Express 文件夹", "เลือก / เปลี่ยนโฟลเดอร์ Express"),
    "browse_title": (
        "请选择 Express 数据所在文件夹",
        "กรุณาเลือกโฟลเดอร์ข้อมูล Express",
    ),
    "browse_hint": (
        "未自动找到 Express,请选择 Express 数据所在文件夹。",
        "ไม่พบ Express โดยอัตโนมัติ กรุณาเลือกโฟลเดอร์ข้อมูล Express",
    ),
    "chk_autostart": ("开机时自动启动小助手", "เริ่มผู้ช่วยอัตโนมัติเมื่อเปิดเครื่อง"),
    "btn_cancel": ("取消", "ยกเลิก"),
    "btn_pair": ("配对", "จับคู่"),
    "pairing": ("正在配对,正在校验配对码…", "กำลังจับคู่ กำลังตรวจสอบรหัส…"),
    "pick_account_first": ("未找到 Express 数据,请选择 Express 文件夹。", "ไม่พบข้อมูล Express กรุณาเลือกโฟลเดอร์ Express"),
    "pair_ok_title": ("配对成功", "จับคู่สำเร็จ"),
    "pair_ok_body": (
        "小助手将在后台运行。",
        "ผู้ช่วยจะทำงานเบื้องหลัง",
    ),
    "pair_fail": ("配对失败:{err}", "จับคู่ไม่สำเร็จ: {err}"),
    # pairing.pair() 内部错误(按 lang 本地化,供配对窗/单测统一走 t())
    "pair_err_token_format": (
        "配对码不完整,请在网页连接窗口点「复制」后整段粘贴",
        "รหัสจับคู่ไม่ครบ กรุณากด「คัดลอก」ในหน้าเว็บแล้ววางทั้งหมด",
    ),
    "pair_err_code_required": (
        "新增连接需要填写配对码。",
        "การเพิ่มการเชื่อมต่อใหม่ต้องระบุรหัสจับคู่",
    ),
    "pair_err_no_chosen": ("请从列表里选一个账套", "กรุณาเลือกชุดข้อมูลบัญชีจากรายการ"),
    "pair_err_dir_missing": (
        "所选账套目录不存在:{dir}",
        "ไม่พบโฟลเดอร์ของชุดข้อมูลที่เลือก: {dir}",
    ),
    "pair_err_dir_empty": (
        "未提供账套目录。",
        "ไม่ได้ระบุโฟลเดอร์ชุดข้อมูลบัญชี",
    ),
    # 账套数据在某盘符(如 S:\…),但本机没有该盘 → 指路:装到做账那台 / 映射该盘。
    "pair_err_drive_missing": (
        "这套账的数据在「{drive}」盘,但这台电脑没有这个盘,所以录不进去。\n"
        "请把小助手装到平时用 Express 记这套账的电脑上(那台有「{drive}」盘);"
        "或在本机把「{drive}」盘映射到与那台相同的位置。",
        "ข้อมูลของชุดบัญชีนี้อยู่ในไดรฟ์ «{drive}» แต่เครื่องนี้ไม่มีไดรฟ์ดังกล่าว จึงบันทึกไม่ได้\n"
        "กรุณาติดตั้งผู้ช่วยบนเครื่องที่ใช้ Express ทำบัญชีชุดนี้เป็นประจำ (เครื่องนั้นมีไดรฟ์ «{drive}») "
        "หรือแมปไดรฟ์ «{drive}» บนเครื่องนี้ให้ชี้ไปยังตำแหน่งเดียวกับเครื่องนั้น",
    ),
    "pair_err_probe_fail": (
        "未能读取 Express 账套,请检查所选文件夹。",
        "ไม่สามารถอ่านชุดบัญชี Express ได้ กรุณาตรวจสอบโฟลเดอร์ที่เลือก",
    ),
    "pair_err_invalid_code": (
        "配对码无效(云端拒绝)",
        "รหัสจับคู่ไม่ถูกต้อง (คลาวด์ปฏิเสธ)",
    ),
    "pair_err_disabled": (
        "此连接已被停用(请联系管理员)",
        "การเชื่อมต่อนี้ถูกปิดใช้งาน (กรุณาติดต่อผู้ดูแลระบบ)",
    ),
    "pair_err_push_off": (
        "云端推送未开启(请管理员开 ERP_PUSH_ENABLED)",
        "ยังไม่ได้เปิดการส่งข้อมูลบนคลาวด์ (ให้ผู้ดูแลระบบเปิด ERP_PUSH_ENABLED)",
    ),
    "pair_err_http": (
        "连云端失败(HTTP {code})",
        "เชื่อมต่อคลาวด์ไม่สำเร็จ (HTTP {code})",
    ),
    "pair_err_network": (
        "连不上 Pearnly,请检查网络后重试。",
        "เชื่อมต่อ Pearnly ไม่ได้ กรุณาตรวจสอบเครือข่ายแล้วลองใหม่",
    ),
    "pair_err_unexpected": (
        "操作未完成,请重试。",
        "ดำเนินการไม่สำเร็จ กรุณาลองใหม่",
    ),
    "pair_err_rejected": ("云端校验未通过", "การตรวจสอบจากคลาวด์ไม่ผ่าน"),
    "pair_err_profile_save": (
        "无法保存连接,请重试。",
        "ไม่สามารถบันทึกการเชื่อมต่อได้ กรุณาลองใหม่",
    ),
    # 多账套管理
    "profiles_title": ("Express 连接", "การเชื่อมต่อ Express"),
    "profiles_intro": (
        "每个连接对应一个 Express 账套,由同一个小助手处理。网页无需保持打开;这里显示的是小助手与 Pearnly 云端桥的连接状态。",
        "แต่ละการเชื่อมต่อตรงกับชุดบัญชี Express หนึ่งชุดและใช้ผู้ช่วยตัวเดียวกัน ไม่ต้องเปิดหน้าเว็บค้างไว้; สถานะนี้คือการเชื่อมต่อระหว่างผู้ช่วยกับบริดจ์คลาวด์ Pearnly",
    ),
    "profiles_lang": ("界面语言", "ภาษา"),
    "profiles_active": ("已启用", "ใช้งาน"),
    "profiles_paused": ("已暂停", "พักการใช้งาน"),
    "profiles_connecting": ("正在连接 Pearnly", "กำลังเชื่อมต่อ Pearnly"),
    "profiles_online": ("Pearnly 已连接", "เชื่อมต่อ Pearnly แล้ว"),
    "profiles_processing": ("处理中", "กำลังประมวลผล"),
    "profiles_attention": ("需处理", "ต้องดำเนินการ"),
    "profiles_offline": ("Pearnly 连接中断", "การเชื่อมต่อ Pearnly ขาดหาย"),
    "profiles_add": ("添加", "เพิ่ม"),
    "profiles_edit": ("编辑", "แก้ไข"),
    "profiles_pause": ("暂停", "พักการใช้งาน"),
    "profiles_resume": ("恢复", "กลับมาใช้งาน"),
    "profiles_remove": ("移除", "ลบ"),
    "profiles_remove_title": ("移除 Express 连接", "ลบการเชื่อมต่อ Express"),
    "profiles_remove_body": (
        "确定移除「{company}」的连接吗? 不会删除 Express 里的数据。",
        "ยืนยันการลบการเชื่อมต่อของ «{company}» หรือไม่? ข้อมูลใน Express จะไม่ถูกลบ",
    ),
    "profiles_error": (
        "无法更新连接,请重试。",
        "ไม่สามารถอัปเดตการเชื่อมต่อได้ กรุณาลองใหม่",
    ),
    # 托盘
    "tray_tooltip": ("Pearnly 小助手", "Pearnly ผู้ช่วย"),
    "tray_starting": ("启动中…", "กำลังเริ่ม…"),
    "tray_open_logs": ("打开日志文件夹", "เปิดโฟลเดอร์บันทึก"),
    "tray_repair": ("重新配对…", "จับคู่ใหม่…"),
    "tray_quit": ("退出", "ออกจากโปรแกรม"),
    "st_idle": ("空闲", "พร้อมทำงาน"),
    "st_working": ("正在录入", "กำลังบันทึก"),
    "st_indexing": (
        "正在整理索引,让 Express 立即可查",
        "กำลังจัดเรียงดัชนีให้ Express ค้นเจอทันที",
    ),
    "st_waiting_lock": (
        "等 Express 释放账套,稍后自动重试",
        "รอ Express ปล่อยชุดบัญชี จะลองใหม่อัตโนมัติ",
    ),
    # 账套被占用弹窗(写盘前探测到独占拿不到 → 原生弹窗交用户:重试 / 终止整批)
    "busy_title": ("账套正被占用", "ชุดข้อมูลบัญชีถูกใช้งานอยู่"),
    "busy_body": (
        "Express 账套正被占用(可能在其他电脑或服务器上开着),暂时无法录入。\n"
        "请关闭正在使用该账套的 Express,然后点「重试」;或点「终止推送」放弃本批。",
        "ชุดข้อมูลบัญชี Express กำลังถูกใช้งานอยู่ (อาจเปิดค้างไว้ที่เครื่องอื่นหรือที่เซิร์ฟเวอร์) จึงยังบันทึกไม่ได้ในขณะนี้\n"
        "กรุณาปิด Express ที่เปิดชุดข้อมูลนี้อยู่ แล้วกด「ลองใหม่」 หรือกด「ยกเลิกการส่ง」เพื่อยกเลิกทั้งชุด",
    ),
    "busy_retry": ("重试", "ลองใหม่"),
    "busy_abort": ("终止推送", "ยกเลิกการส่ง"),
    "log_busy_aborted": (
        "账套被占用,已按选择终止本批推送(退回云端显示失败)。",
        "ชุดข้อมูลถูกใช้งานอยู่ จึงยกเลิกการส่งทั้งชุดตามที่เลือก (ส่งกลับคลาวด์เป็นไม่สำเร็จ)",
    ),
    "st_error": ("出错", "เกิดข้อผิดพลาด"),
    "st_offline": ("离线", "ออฟไลน์"),
    "st_connected": ("已连接,待命中", "เชื่อมต่อแล้ว พร้อมทำงาน"),
    "st_stalled_giveup": (
        "连接异常,请检查账套路径或网络后重开小助手",
        "การเชื่อมต่อผิดปกติ กรุณาตรวจสอบที่อยู่ชุดบัญชีหรือเครือข่าย แล้วเปิดผู้ช่วยใหม่",
    ),
    # 「设置 / 重新配对」= 单一入口(旧版分「打开设置」「重新配对」两项、开的却是同一窗)。
    "tray_open_settings": ("设置 / 重新配对", "ตั้งค่า / จับคู่ใหม่"),
    # 重新配对/设置:配对码、密码留空 = 沿用当前(不改),只改账套/科目。
    "ph_code_keep": (
        "留空 = 沿用当前连接(不更换密钥)",
        "เว้นว่าง = ใช้การเชื่อมต่อเดิม (ไม่เปลี่ยนรหัส)",
    ),
    # 离线友好原因(替代英文异常类名 HTTPStatusError):
    "off_auth": ("连接密钥已失效,请重新配对", "รหัสเชื่อมต่อหมดอายุ กรุณาจับคู่ใหม่"),
    "off_disabled": ("此连接已被停用", "การเชื่อมต่อนี้ถูกปิดใช้งาน"),
    "off_push_off": ("云端推送未开启", "ยังไม่ได้เปิดการส่งข้อมูลบนคลาวด์"),
    "off_network": (
        "连不上 Pearnly,请检查网络",
        "เชื่อมต่อ Pearnly ไม่ได้ กรุณาตรวจสอบเครือข่าย",
    ),
    # 活动记录(activity.log · 客户「打开日志」看的人话事件):
    "log_started": (
        "小助手已启动,正在后台待命。",
        "ผู้ช่วยเริ่มทำงานแล้ว กำลังรอในเบื้องหลัง",
    ),
    "log_connected": (
        "已连上 Pearnly,等待新发票。",
        "เชื่อมต่อ Pearnly แล้ว กำลังรอใบกำกับใหม่",
    ),
    "log_offline": ("与 Pearnly 断开:{reason}", "ขาดการเชื่อมต่อกับ Pearnly: {reason}"),
    "log_self_healed": (
        "小助手检测到卡顿,已自动重启恢复(无需手动操作)。",
        "ผู้ช่วยตรวจพบการค้าง และรีสตาร์ทให้เองแล้ว (ไม่ต้องดำเนินการเอง)",
    ),
    "log_recorded": ("已录入 1 张发票({inv})。", "บันทึกใบกำกับแล้ว 1 ใบ ({inv})"),
    "log_record_fail": ("一张发票录入失败:{err}", "บันทึกใบกำกับไม่สำเร็จ: {err}"),
    "log_paired": ("配对成功,账套:{company}。", "จับคู่สำเร็จ ชุดข้อมูล: {company}"),
    "log_quit": ("小助手已退出。", "ผู้ช่วยปิดทำงานแล้ว"),
    # 自动更新
    "tray_update_to": ("更新到最新版 v{ver}", "อัปเดตเป็นเวอร์ชันล่าสุด v{ver}"),
    # 录入并重建索引完成(每张写盘后即重建索引 · 真可查)→ 系统原生气泡
    "notif_recorded_title": ("已录入 Express", "บันทึกเข้า Express แล้ว"),
    "notif_recorded_one": (
        "{inv} 已录入 Express,现在可在 Express 查到。",
        "{inv} บันทึกเข้า Express แล้ว ตอนนี้ค้นหาใน Express ได้เลย",
    ),
    "notif_recorded_many": (
        "本批 {n} 张已录入 Express,现在都能查到。",
        "ชุดนี้ {n} ใบบันทึกเข้า Express แล้ว ตอนนี้ค้นหาได้ทั้งหมด",
    ),
    # 本批有失败 → 同样弹原生通知(诚实反馈·不拿失败冒充成功)
    "notif_partial_title": ("本批部分录入", "บันทึกบางส่วน"),
    "notif_partial_body": (
        "本批 {ok} 张已录入,{fail} 张未成功(需在网页处理)。",
        "ชุดนี้ {ok} ใบบันทึกแล้ว · {fail} ใบไม่สำเร็จ (จัดการบนเว็บ)",
    ),
    "notif_failed_title": ("本批未成功", "ชุดนี้ไม่สำเร็จ"),
    "notif_failed_body": (
        "本批 {fail} 张未录入,请打开网页日志查看原因。",
        "ชุดนี้ {fail} ใบไม่ได้บันทึก เปิดบันทึกบนเว็บเพื่อดูสาเหตุ",
    ),
    "update_avail_title": ("有新版本", "มีเวอร์ชันใหม่"),
    "update_avail_body": (
        "Pearnly 小助手 v{ver} 可更新。右键托盘 →「更新到最新版」一键更新。",
        "มี Pearnly ผู้ช่วย v{ver} ให้อัปเดต คลิกขวาที่ไอคอน →「อัปเดตเป็นเวอร์ชันล่าสุด」",
    ),
    "update_downloading": (
        "正在下载更新,稍后会自动重启…",
        "กำลังดาวน์โหลดอัปเดต เดี๋ยวจะรีสตาร์ทเอง…",
    ),
    "update_failed": (
        "更新失败,请稍后重试或从网页重新下载安装。",
        "อัปเดตไม่สำเร็จ ลองใหม่ภายหลังหรือดาวน์โหลดติดตั้งใหม่จากเว็บ",
    ),
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
