# -*- coding: utf-8 -*-
"""DMS 错误码 → 给用户看的话（多语言）。

**为什么光有错误码不够**：`ERR_DMS_CUSTOMER_SAVE` 这种码能让人去查日志，但不能拿给
操作员看。而且同一类失败要说清"是 DMS 拒了"还是"我们复核没查到"—— 那决定用户下一步
该干什么（改字段重试 / 找管理员 / 直接再试一次）。

照搬老产品 `services/erp/erp_dms_push.py` 里的 `_DMS_FRIENDLY`，**文案一字未改**：
这些话是给泰国车行操作员看的，改一个字都可能让他们做错事。五种语言全带（老产品支持
哪几种我们就要有哪几种，不能少）。
"""

from __future__ import annotations

from typing import Dict

FRIENDLY: Dict[str, Dict[str, str]] = {
    "ERR_UNEXPECTED": {
        "zh": "服务器内部错误,请联系管理员",
        "en": "Internal server error, please contact admin",
        "th": "ข้อผิดพลาดของเซิร์ฟเวอร์ภายใน กรุณาติดต่อผู้ดูแล",
        "zh_TW": "伺服器內部錯誤,請聯絡管理員",
        "ja": "サーバー内部エラーが発生しました。管理者にお問い合わせください",
    },
    "ERR_PLAYWRIGHT_MISSING": {
        "zh": "服务器缺少 Playwright 依赖,请联系运维",
        "en": "Server is missing the Playwright dependency",
        "th": "เซิร์ฟเวอร์ขาดไลบรารี Playwright",
        "zh_TW": "伺服器缺少 Playwright 依賴",
        "ja": "サーバーに Playwright 依存関係がありません。運用担当者にお問い合わせください",
    },
    "ERR_KMS_MISSING": {
        "zh": "服务器加密密钥未配置,请联系运维",
        "en": "Server KMS key is not configured",
        "th": "เซิร์ฟเวอร์ยังไม่ตั้งค่าคีย์เข้ารหัส",
        "zh_TW": "伺服器加密金鑰未設定",
        "ja": "サーバーの暗号化キーが設定されていません。運用担当者にお問い合わせください",
    },
    "ERR_NO_CREDS": {
        "zh": "请输入用户名和密码",
        "en": "Username and password required",
        "th": "กรุณากรอกชื่อผู้ใช้และรหัสผ่าน",
        "zh_TW": "請輸入使用者名稱和密碼",
        "ja": "ユーザー名とパスワードを入力してください",
    },
    "ERR_CRED_DECRYPT": {
        "zh": "凭据解密失败 · 请重新输入用户名和密码",
        "en": "Credential decrypt failed — please re-enter username and password",
        "th": "ถอดรหัสข้อมูลรับรองไม่สำเร็จ",
        "zh_TW": "憑證解密失敗 · 請重新輸入",
        "ja": "認証情報の復号に失敗しました · ユーザー名とパスワードを再入力してください",
    },
    "ERR_DMS_AUTH": {
        "zh": "DMS 登录失败 · 请检查用户名和密码",
        "en": "DMS login failed — check username and password",
        "th": "เข้าสู่ระบบ DMS ไม่สำเร็จ — ตรวจสอบชื่อผู้ใช้และรหัสผ่าน",
        "zh_TW": "DMS 登入失敗 · 請檢查使用者名稱和密碼",
        "ja": "DMS へのログインに失敗しました · ユーザー名とパスワードをご確認ください",
    },
    "ERR_DMS_TECHNICAL": {
        "zh": "连接 DMS 超时或网络异常 · 请稍后重试",
        "en": "DMS connection timed out — please retry",
        "th": "การเชื่อมต่อ DMS หมดเวลา — กรุณาลองใหม่",
        "zh_TW": "連線 DMS 逾時或網路異常 · 請稍後重試",
        "ja": "DMS への接続がタイムアウトまたはネットワーク異常です · しばらくしてから再試行してください",
    },
    "ERR_DMS_CONCURRENT_LOGIN": {
        "zh": "DMS 账号正在其他地方使用 · 请退出其他登录后重试",
        "en": "This DMS account is being used elsewhere — sign out there and retry",
        "th": "บัญชี DMS นี้กำลังถูกใช้งานที่อื่น · กรุณาออกจากระบบที่อื่นแล้วลองใหม่",
        "zh_TW": "DMS 帳號正在其他地方使用 · 請退出其他登入後重試",
        "ja": "この DMS アカウントは別の場所で使用中です · 他の場所からログアウトして再試行してください",
    },
    "ERR_DMS_ADMIN_AUTH": {
        "zh": "DMS 管理员凭据登录失败 · 请检查管理员用户名和密码",
        "en": "DMS admin credential login failed — check the admin username and password",
        "th": "เข้าสู่ระบบด้วยบัญชีผู้ดูแล DMS ไม่สำเร็จ — ตรวจสอบชื่อผู้ใช้และรหัสผ่านผู้ดูแล",
        "zh_TW": "DMS 管理員憑證登入失敗 · 請檢查管理員使用者名稱和密碼",
        "ja": "DMS 管理者資格情報でのログインに失敗しました · 管理者のユーザー名とパスワードをご確認ください",
    },
    "ERR_DMS_CUSTOMER_SAVE": {
        "zh": "保存客户到 DMS 失败 · 请检查字段后重试",
        "en": "Failed to save the customer to DMS — check the fields and retry",
        "th": "บันทึกลูกค้าเข้า DMS ไม่สำเร็จ — ตรวจสอบข้อมูลแล้วลองใหม่",
        "zh_TW": "儲存客戶到 DMS 失敗 · 請檢查欄位後重試",
        "ja": "DMS への顧客保存に失敗しました · 項目を確認して再試行してください",
    },
    "ERR_DMS_IMPORT": {
        "zh": "建订车单失败 · 请稍后重试",
        "en": "Failed to create the booking — please retry",
        "th": "สร้างใบจองรถไม่สำเร็จ — กรุณาลองใหม่",
        "zh_TW": "建立訂車單失敗 · 請稍後重試",
        "ja": "予約の作成に失敗しました · 再試行してください",
    },
    "ERR_DMS_IMPORT_REPORT": {
        "zh": "订车单导入被 DMS 拒绝 · 请检查订车资料",
        "en": "DMS rejected the booking import — check the booking details",
        "th": "DMS ปฏิเสธการนำเข้าใบจอง — ตรวจสอบรายละเอียดการจอง",
        "zh_TW": "訂車單匯入被 DMS 拒絕 · 請檢查訂車資料",
        "ja": "DMS が予約取込を拒否しました · 予約内容をご確認ください",
    },
    "ERR_DMS_BOOKING_PATCH": {
        "zh": "回填订车单资料失败 · 请稍后重试",
        "en": "Failed to fill in the booking details — please retry",
        "th": "เติมข้อมูลใบจองไม่สำเร็จ — กรุณาลองใหม่",
        "zh_TW": "回填訂車單資料失敗 · 請稍後重試",
        "ja": "予約情報の入力に失敗しました · 再試行してください",
    },
    "ERR_DMS_ADVISOR_REQUIRED": {
        "zh": "这张订车单还没定销售顾问 · 请联系管理员为该 DMS 账号配置销售顾问后重开单",
        "en": "This booking has no sales advisor assigned — ask your admin to set the advisor for this DMS account, then start the booking again",
        "th": "ใบจองนี้ยังไม่ได้กำหนดที่ปรึกษาการขาย กรุณาให้ผู้ดูแลกำหนดที่ปรึกษาให้บัญชี DMS นี้ แล้วเปิดใบจองใหม่",
        "zh_TW": "這張訂車單還沒定銷售顧問 · 請聯絡管理員為該 DMS 帳號設定銷售顧問後重開單",
        "ja": "この予約には販売アドバイザーが設定されていません · 管理者に該当 DMS アカウントのアドバイザー設定を依頼してから、予約を作成し直してください",
    },
    "ERR_DMS_ADVISOR_UNMATCHED": {
        "zh": "在 DMS 销售顾问名册里找不到该顾问 · 请让管理员在 DMS 顾问名册补上这个账号,或联系 Pearnly 配置归属",
        "en": "The advisor is not in the DMS advisor list — ask your admin to add this account to the DMS advisor list, or contact Pearnly to configure it",
        "th": "ไม่พบที่ปรึกษาการขายรายนี้ในรายชื่อที่ปรึกษาของ DMS กรุณาให้ผู้ดูแลเพิ่มบัญชีนี้ในรายชื่อที่ปรึกษาการขาย หรือติดต่อทีม Pearnly เพื่อกำหนดให้",
        "zh_TW": "在 DMS 銷售顧問名冊裡找不到該顧問 · 請讓管理員在 DMS 顧問名冊補上這個帳號,或聯絡 Pearnly 設定歸屬",
        "ja": "このアドバイザーは DMS のアドバイザー名簿に存在しません · 管理者に当該アカウントの追加を依頼するか、Pearnly にご連絡ください",
    },
    "ERR_DMS_TEMPLATE": {
        "zh": "下载订车单模板失败 · 请稍后重试",
        "en": "Failed to download the booking template — please retry",
        "th": "ดาวน์โหลดเทมเพลตใบจองไม่สำเร็จ — กรุณาลองใหม่",
        "zh_TW": "下載訂車單範本失敗 · 請稍後重試",
        "ja": "予約テンプレートのダウンロードに失敗しました · 再試行してください",
    },
    "ERR_DMS_MASTER_UNAVAILABLE": {
        "zh": "DMS 主档暂时无法读取 · 请稍后重试",
        "en": "DMS master data is temporarily unavailable — please retry",
        "th": "อ่านข้อมูลหลักของ DMS ไม่ได้ชั่วคราว — กรุณาลองใหม่อีกครั้ง",
        "zh_TW": "DMS 主檔暫時無法讀取 · 請稍後重試",
        "ja": "DMS のマスターデータを一時的に読み取れません · しばらくしてから再試行してください",
    },
    "ERR_DMS_MASTER_EMPTY": {
        "zh": "DMS 尚未配置这个主档选项 · 请让管理员在 DMS 添加后重试",
        "en": "This DMS master option has not been configured. Ask an admin to add it in DMS, then retry.",
        "th": "ยังไม่ได้ตั้งค่าตัวเลือกข้อมูลหลักนี้ใน DMS กรุณาให้ผู้ดูแลเพิ่มข้อมูลใน DMS แล้วลองใหม่อีกครั้ง",
        "zh_TW": "DMS 尚未設定這個主檔選項 · 請讓管理員在 DMS 新增後重試",
        "ja": "この DMS マスター項目は未設定です。管理者が DMS に追加してから再試行してください",
    },
    "ERR_DMS_MASTER_CHANGED": {
        "zh": "DMS 主档刚刚发生变化 · 系统已更新摘要 · 请重新核对并再次确认",
        "en": "DMS master data just changed. The summary has been updated; review it and confirm again.",
        "th": "ข้อมูลหลักของ DMS เพิ่งมีการเปลี่ยนแปลง ระบบอัปเดตสรุปแล้ว กรุณาตรวจสอบและยืนยันอีกครั้ง",
        "zh_TW": "DMS 主檔剛剛發生變更 · 系統已更新摘要 · 請重新核對並再次確認",
        "ja": "DMS マスターデータが変更されました。更新後の概要を確認し、もう一度確定してください",
    },
    "ERR_DMS_MASTER_UNMATCHED": {
        "zh": "所选项目在 DMS 主档里已变更或不存在 · 请返回重新选择后重试",
        "en": "The selected item is no longer in the DMS master data — go back, re-select it, and retry",
        "th": "รายการที่เลือกไม่อยู่ในข้อมูลหลักของ DMS แล้ว กรุณากลับไปเลือกใหม่ แล้วลองอีกครั้ง",
        "zh_TW": "所選項目在 DMS 主檔裡已變更或不存在 · 請返回重新選擇後重試",
        "ja": "選択した項目は DMS のマスターデータに存在しなくなりました · 戻って選択し直してから再試行してください",
    },
}


def friendly(code: str) -> Dict[str, str]:
    """错误码 → 多语言文案。**认不出的码一律回"服务器内部错误"**，
    绝不把原始堆栈或英文异常丢给操作员看。"""
    return FRIENDLY.get(code, FRIENDLY["ERR_UNEXPECTED"])
