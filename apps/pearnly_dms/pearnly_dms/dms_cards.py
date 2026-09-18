# -*- coding: utf-8 -*-
"""DMS LINE 对话的泰语文案与字段标签（**单一来源**）。

文案集中在一处，测试才能照着断言；散在各个 handler 里就没人说得清"用户到底会看到什么"。
**面向的是经销商销售员**，所以：简洁、不堆 emoji、不用书面语。

照搬老产品 `services/line_dms/cards.py` 的**数据部分**（文案、按钮、动作名、字段标签）；
卡片布局构造（Flex JSON）是下一批要搬的，搬上来时也放这个模块。

⚠️ 这些泰文一字未改 —— 它们是泰方逐句定过的。
"""

from __future__ import annotations

from typing import Dict

# ── 文案 ─────────────────────────────────────────────────────────────────
TXT_BLURRY = "อ่านบัตรไม่ชัด กรุณาถ่ายใหม่"
TXT_ASK_PHONE = "รับรูปบัตรแล้ว กรุณาพิมพ์เบอร์โทรศัพท์ของลูกค้า"
TXT_ASK_CARD = "รับเบอร์โทรแล้ว กรุณาถ่ายรูปบัตรประชาชนของลูกค้า"
TXT_RESET = "เริ่มรายการใหม่แล้ว ถ่ายรูปบัตรประชาชนเพื่อเริ่มบันทึกลูกค้า"
TXT_INTRO = "ถ่ายรูปบัตรประชาชนของลูกค้าเพื่อเริ่มบันทึกลูกค้าใหม่"
TXT_NEED_BOTH = "กรุณาส่งรูปบัตรประชาชนและเบอร์โทรศัพท์ของลูกค้า"
TXT_PICK_ABOVE = "กรุณาเลือกรายการจากการ์ดด้านบน หรือพิมพ์ เริ่มใหม่ เพื่อเริ่มรายการใหม่"
TXT_EXPIRED = "รายการหมดอายุ เริ่มใหม่อีกครั้ง"
TXT_SAME = "ลูกค้าท่านนี้มีข้อมูลอยู่แล้ว ข้อมูลตรงกัน"
TXT_KEEP = "ใช้ข้อมูลเดิมต่อ"
TXT_ADMIN_NEEDED = "ต้องตั้งค่าบัญชีแอดมินใน Pearnly ก่อนจึงจะอัปเดตได้"
TXT_ADMIN_AUTH_FAIL = "อัปเดตไม่สำเร็จ กรุณาแจ้งผู้ดูแลตรวจสอบรหัสแอดมินใน Pearnly"
TXT_NO_ENDPOINT = "ยังไม่ได้ตั้งค่าการเชื่อมต่อ DMS กรุณาติดต่อผู้ดูแล"
# 402 指路：销售员治不了余额，把动作交给公司管理员并给自助入口，不留死胡同
TXT_NO_CREDIT = "เครดิตไม่เพียงพอ กรุณาติดต่อผู้ดูแลระบบของบริษัทเพื่อเติมเงินหรือสมัครแพ็กเกจที่ pearnly.com/dms"
TXT_SYSTEM_ERROR = "ระบบขัดข้อง กรุณาลองใหม่อีกครั้ง"
TXT_LOOKUP_FAIL = "ตรวจสอบข้อมูลลูกค้าไม่สำเร็จ กรุณาลองใหม่"
TXT_SAVE_FAIL = "บันทึกไม่สำเร็จ กรุณาลองใหม่"

# 菜单层：欢迎语 → 选建档或订车
TXT_MENU_TITLE = "ยินดีต้อนรับเข้าสู่ระบบ Pearnly DMS"
TXT_MENU_SUBTITLE = "กรุณาเลือกเมนูด้านล่างเพื่อเริ่มต้นใช้งาน"
TXT_MENU_SEND_CARD = "กรุณาส่งรูปบัตรประชาชนของลูกค้า"
# 菜单1（只建档）收尾：**不提订车**（泰方拍板），真写档与零写入分开话术
TXT_DONE_SAVED = "บันทึกข้อมูลลูกค้าเรียบร้อยแล้ว พิมพ์ เมนู เพื่อทำรายการอื่น"
TXT_DONE_SAME = "ใช้ข้อมูลเดิมเรียบร้อย พิมพ์ เมนู เพื่อทำรายการอื่น"
TXT_RETAKE = "กรุณาถ่ายรูปบัตรประชาชนใหม่อีกครั้ง"

BTN_SAVE_NEW = "บันทึกลูกค้าใหม่"
BTN_RESTART = "เริ่มใหม่"
BTN_UPDATE = "อัปเดตข้อมูล"
BTN_KEEP = "ใช้ข้อมูลเดิม"
BTN_NEW_CUSTOMER = "ลูกค้าใหม่"
BTN_EDIT = "แก้ไข"
BTN_EDIT_CANCEL = "ยกเลิก"

TXT_MENU_GREETING = "สวัสดีค่ะ 🙏\n" + TXT_MENU_TITLE + "\n" + TXT_MENU_SUBTITLE
TXT_MENU_PICK = "กรุณาเลือกเมนูที่ต้องการ"
TXT_MENU_PICK_SUB = "เลือกรายการเพื่อดำเนินการต่อ"
TXT_MENU_ITEM1 = "จัดทำข้อมูลลูกค้า"
TXT_MENU_D1 = "จัดทำข้อมูลลูกค้าใหม่จากบัตรประชาชน หรืออัปเดตข้อมูลลูกค้าเดิม"
TXT_MENU_ITEM2 = "จัดทำใบจอง"
TXT_MENU_D2 = "สร้างใบจองรถยนต์ เลือกรุ่น สี และบันทึกการจอง"
TXT_MENU_ITEM3 = "เข้าสู่ระบบ DMS"
TXT_MENU_D3 = "เปิด Pearnly DMS ได้สะดวก โดยไม่ต้องพิมพ์รหัสผ่านอีกครั้ง"
TXT_MENU_ITEM4 = "เปลี่ยนบัญชีและรหัสผ่าน"
TXT_MENU_D4 = "อัปเดตชื่อผู้ใช้และรหัสผ่าน DMS ที่บันทึกไว้ใน Pearnly"
TXT_MENU_ITEM5 = "ค้นหาข้อมูล"
TXT_MENU_D5 = "ดูข้อมูลการขายล่าสุดจาก DMS ตามสิทธิ์ที่ได้รับ"
TXT_MENU_HINT = "พิมพ์ เมนู เพื่อเรียกเมนูนี้ได้ตลอดเวลา"
BTN_CONTINUE_BOOKING = "ทำใบจองต่อ"
BTN_SUBMIT_APPROVAL = "ส่งขออนุมัติ"

# 逐字段修正：可改字段与地道泰文标签（顺序 = quick reply 呈现顺序）。
# 地理四级 id（จังหวัด/อำเภอ/ตำบล/รหัสไปรษณีย์）**不进此表** ——
# 改地址文本后由既有级联匹配重新解析。
EDIT_FIELDS = (
    ("name", "ชื่อ-นามสกุล"),
    ("people_id", "เลขบัตรประชาชน"),
    ("birthday_be", "วันเกิด"),
    ("phone", "เบอร์โทร"),
    ("house_no", "บ้านเลขที่"),
    ("moo", "หมู่"),
    ("soi", "ซอย"),
    ("road", "ถนน"),
)
_EDIT_LABELS = dict(EDIT_FIELDS)

TXT_EDIT_PICK = "ต้องการแก้ไขข้อมูลใด"
TXT_EDIT_CANCELLED = "ยกเลิกการแก้ไขแล้ว"
TXT_EDIT_BAD_ID = (
    "เลขบัตรประชาชนไม่ถูกต้อง (เลขตรวจสอบท้ายบัตรไม่ผ่าน) กรุณาตรวจสอบและพิมพ์ใหม่อีกครั้ง"
)
TXT_EDIT_BAD_BIRTHDAY = "วันเกิดไม่ถูกต้อง กรุณาพิมพ์เป็น วว/ดด/ปปปป (พ.ศ.)"
TXT_EDIT_BAD_PHONE = "กรุณาพิมพ์เบอร์โทรศัพท์"
TXT_EDIT_EMPTY = "กรุณาพิมพ์ข้อมูลให้ครบถ้วน"

# 动作名（flow 侧 dispatch 用同一常量，避免拼写漂移）
ACT_CREATE = "create_new"
ACT_UPDATE = "update"
ACT_KEEP = "keep"
ACT_RESET = "reset"
ACT_PICK = "pick"
ACT_EDIT = "edit"
ACT_EDIT_FIELD = "edit_field"
ACT_EDIT_CANCEL = "edit_cancel"
ACT_MENU_CUSTOMER = "menu_customer"
ACT_MENU_BOOKING = "menu_booking"
ACT_MENU_QUERY = "menu_query"
ACT_CONTINUE_BOOKING = "continue_booking"
ACT_RETAKE = "retake"
ACT_CONFIRM_BOOKING = "confirm_booking"
ACT_CANCEL_BOOKING = "cancel_booking"
ACT_RETRY_BOOKING = "retry_booking"
ACT_SUBMIT_APPROVAL = "approval_submit"
ACT_APPROVAL_TARGET = "approval_target"
ACT_APPROVAL_APPROVE = "approval_approve"
ACT_APPROVAL_REJECT = "approval_reject"
ACT_APPROVAL_RETARGET = "approval_retarget"

# 订车阶段文案与按钮
BTN_CONFIRM_BOOKING = "ยืนยันจอง"
BTN_CANCEL_BOOKING = "ยกเลิก"
BTN_RETRY_BOOKING = "ลองสร้างใบจองอีกครั้ง"
TXT_BOOKING_CANCELLED = "ทิ้งรายการแล้ว ไม่มีข้อมูลถูกบันทึก · พิมพ์ เมนู เพื่อเริ่มใหม่"
TXT_BOOKING_FAIL = "สร้างใบจองไม่สำเร็จ กรุณาลองใหม่"

# 差异卡上的字段名（泰文）—— 只列会出现在 diff 里的那些
FIELD_LABELS_TH: Dict[str, str] = {
    "prefix_id": "คำนำหน้า",
    "name": "ชื่อ",
    "birthday_be": "วันเกิด",
    "phone": "เบอร์โทร",
    "house_no": "บ้านเลขที่",
    "moo": "หมู่",
    "soi": "ซอย",
    "road": "ถนน",
    "province_id": "จังหวัด",
    "district_id": "อำเภอ",
    "subdistrict_id": "ตำบล",
    "zipcode_id": "รหัสไปรษณีย์",
}
