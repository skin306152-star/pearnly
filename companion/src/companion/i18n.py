# -*- coding: utf-8 -*-
"""客户可见文案的四语层(泰语 TH + 中文 ZH + 英语 EN + 日语 JA)· 配对窗 + 托盘 + 提示/报错统一走 t(key,lang)。

中文为权威文案(专业 · 非口语);泰语为地道专业泰语(客户语言);英文、日文用词同网页(frontend/assistant/src/i18n.ts
与 apps/*/*/i18n/messages.tsv)。四种语言每条都要有(scripts/check-copy.py 的 R-LANG 拦)。
默认按 Windows 语言(泰→th / 中→zh / 英→en / 日→ja / 其它→th);对话页切换语言时同步过来,
存进 config.ui_lang 记住。
红线:不出现甩锅/口语(「是你的责任」「自动干活」「选你自家那个」等)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

LANGS = ("th", "zh", "en", "ja")
_DEFAULT = "th"

# key: (zh, th, en, ja)。带 {n}/{company}/{err} 占位的用 t(..., n=) 格式化。
_M = {
    "lang_toggle": ("中文 / ไทย", "中文 / ไทย", "中文 / ไทย", "中文 / ไทย"),
    "win_title_add": ("添加成员", "เพิ่มสมาชิก", "Add member", "メンバーを追加"),
    "win_title_edit": ("更换配对码", "เปลี่ยนรหัสจับคู่", "Change pairing code", "ペアリングコードを変更"),
    "already_running_title": ("CC", "CC", "CC", "CC"),
    "already_running_body": (
        "CC 已在运行，请在系统托盘（任务栏右下角）中查看。",
        "CC กำลังทำงานอยู่แล้ว กรุณาตรวจสอบที่ถาดระบบ (มุมขวาล่างของแถบงาน)",
        "CC is already running. Find it in the system tray (bottom right of the taskbar).",
        "CC はすでに起動しています。システムトレイ（タスクバーの右下）で確認してください。",
    ),
    "lbl_code": ("配对码", "รหัสจับคู่", "Pairing code", "ペアリングコード"),
    "ph_code": (
        "在 Pearnly 网页「连接 Express」中获取",
        "รับได้จากหน้า「เชื่อมต่อ Express」บนเว็บ Pearnly",
        "Get it from “Connect Express” on the Pearnly website",
        "Pearnly ウェブの「Express に接続」で取得",
    ),
    "status_scanning": ("正在查找…", "กำลังค้นหา…", "Searching…", "検索しています…"),
    # 主窗口「这台电脑」：Express 账套是全局设置（检测到的数据根全部授权，网页再选年度 / 账套）。
    "sec_machine": ("本机", "เครื่องนี้", "This computer", "このパソコン"),
    "lbl_express": ("Express 账套", "ชุดบัญชี Express", "Express account set", "Express の会計データ"),
    "sec_members": ("成员", "สมาชิก", "Members", "メンバー"),
    "express_roots_more": ("{path} 等 {n} 个年度", "{path} รวม {n} ปีบัญชี", "{path} · {n} years", "{path} ほか {n} 年度"),
    "status_reg_error": (
        "无法读取 Express 公司列表，请确认 Express 路径。",
        "ไม่สามารถอ่านรายชื่อบริษัทจาก Express ได้ กรุณาตรวจสอบตำแหน่งโฟลเดอร์ Express",
        "Could not read the Express company list. Check the Express path.",
        "Express の会社一覧を読み取れません。Express のパスを確認してください。",
    ),
    "btn_browse": ("更换…", "เปลี่ยน…", "Change…", "変更…"),
    "express_not_found": (
        "没有找到 Express 数据。",
        "ไม่พบข้อมูล Express",
        "Express data not found.",
        "Express のデータが見つかりません。",
    ),
    "op_login_title": ("Express 登录", "เข้าสู่ระบบ Express", "Express sign-in", "Express ログイン"),
    "op_login_user": ("用户名", "ชื่อผู้ใช้", "User name", "ユーザー名"),
    "op_login_password": ("密码", "รหัสผ่าน", "Password", "パスワード"),
    "mrerp_login_title": ("MR.ERP 登录", "เข้าสู่ระบบ MR.ERP", "MR.ERP sign-in", "MR.ERP ログイン"),
    "browse_title": (
        "请选择 Express 数据所在文件夹",
        "กรุณาเลือกโฟลเดอร์ข้อมูล Express",
        "Choose the Express data folder",
        "Express のデータフォルダーを選択してください",
    ),
    "browse_hint": (
        "未找到 Express，请点击「更换…」选择 Express 数据所在文件夹。",
        "ไม่พบ Express กรุณากด «เปลี่ยน…» แล้วเลือกโฟลเดอร์ข้อมูล Express",
        "Express not found. Click “Change…” and choose the Express data folder.",
        "Express が見つかりません。「変更…」をクリックして Express のデータフォルダーを選択してください。",
    ),
    "chk_autostart": (
        "开机时自动启动 CC",
        "เริ่ม CC อัตโนมัติเมื่อเปิดเครื่อง",
        "Start CC when the computer starts",
        "パソコンの起動時に CC を自動で起動",
    ),
    "btn_cancel": ("取消", "ยกเลิก", "Cancel", "キャンセル"),
    "btn_pair": ("配对", "จับคู่", "Pair", "ペアリング"),
    "btn_save": ("保存", "บันทึก", "Save", "保存"),
    "pairing": ("正在校验配对码…", "กำลังตรวจสอบรหัสจับคู่…", "Checking pairing code…", "ペアリングコードを確認しています…"),
    "pair_fail": ("配对失败：{err}", "จับคู่ไม่สำเร็จ: {err}", "Pairing failed: {err}", "ペアリングに失敗しました：{err}"),
    # pairing.pair() 内部错误(按 lang 本地化,供配对窗/单测统一走 t())
    "pair_err_token_format": (
        "配对码不完整，请在网页连接窗口点击「复制」后完整粘贴。",
        "รหัสจับคู่ไม่ครบถ้วน กรุณากด「คัดลอก」ในหน้าต่างเชื่อมต่อบนเว็บ แล้ววางรหัสทั้งหมด",
        "The pairing code is incomplete. Click “Copy” in the connection window on the website and paste the whole code.",
        "ペアリングコードが不完全です。ウェブの接続ウィンドウで「コピー」をクリックし、コード全体を貼り付けてください。",
    ),
    "pair_err_code_required": ("请填写配对码。", "กรุณากรอกรหัสจับคู่", "Enter the pairing code.", "ペアリングコードを入力してください。"),
    "pair_err_no_chosen": (
        "请从列表中选择账套",
        "กรุณาเลือกชุดบัญชีจากรายการ",
        "Choose an account set from the list",
        "一覧から会計データを選択してください",
    ),
    "pair_err_dir_missing": (
        "所选账套目录不存在：{dir}",
        "ไม่พบโฟลเดอร์ของชุดบัญชีที่เลือก: {dir}",
        "The selected account set folder does not exist: {dir}",
        "選択した会計データのフォルダーがありません：{dir}",
    ),
    "pair_err_dir_empty": (
        "未提供账套目录。",
        "ไม่ได้ระบุโฟลเดอร์ชุดบัญชี",
        "No account set folder provided.",
        "会計データのフォルダーが指定されていません。",
    ),
    # 账套数据在某盘符(如 S:\…),但本机没有该盘 → 指路:装到做账那台 / 映射该盘。
    "pair_err_drive_missing": (
        "本机没有「{drive}」盘。请在录入此账套的电脑上安装 CC 桌面版，或将该盘映射到相同的网络位置。",
        "เครื่องนี้ไม่มีไดรฟ์ «{drive}» กรุณาติดตั้ง CC เดสก์ท็อปบนเครื่องที่บันทึกชุดบัญชีนี้ หรือแมปไดรฟ์นี้ไปยังตำแหน่งเครือข่ายเดียวกัน",
        "No “{drive}” drive on this computer. Install CC Desktop on the computer used for this account set, or map the drive to its network location.",
        "このパソコンに「{drive}」ドライブがありません。この会計データを入力するパソコンに CC デスクトップをインストールするか、このドライブを同じネットワークの場所に割り当ててください。",
    ),
    "pair_err_probe_fail": (
        "未能读取 Express 账套，请检查所选文件夹。",
        "ไม่สามารถอ่านชุดบัญชี Express ได้ กรุณาตรวจสอบโฟลเดอร์ที่เลือก",
        "Could not read the Express account set. Check the selected folder.",
        "Express の会計データを読み取れません。選択したフォルダーを確認してください。",
    ),
    "pair_err_invalid_code": ("配对码无效", "รหัสจับคู่ไม่ถูกต้อง", "Invalid pairing code", "ペアリングコードが無効です"),
    "pair_err_disabled": (
        "此连接已停用，请联系管理员。",
        "การเชื่อมต่อนี้ถูกปิดใช้งานแล้ว กรุณาติดต่อผู้ดูแลระบบ",
        "This connection has been disabled. Contact your administrator.",
        "この接続は無効になっています。管理者に連絡してください。",
    ),
    "pair_err_push_off": (
        "云端推送未启用，请联系管理员。",
        "ยังไม่ได้เปิดใช้การส่งข้อมูลบนคลาวด์ กรุณาติดต่อผู้ดูแลระบบ",
        "Cloud push is not enabled. Contact your administrator.",
        "クラウドへの送信が有効になっていません。管理者に連絡してください。",
    ),
    "pair_err_http": (
        "连接云端失败（HTTP {code}）",
        "เชื่อมต่อคลาวด์ไม่สำเร็จ (HTTP {code})",
        "Could not connect to the cloud (HTTP {code})",
        "クラウドに接続できません（HTTP {code}）",
    ),
    "pair_err_network": (
        "无法连接 Pearnly，请检查网络后重试。",
        "ไม่สามารถเชื่อมต่อ Pearnly ได้ กรุณาตรวจสอบเครือข่ายแล้วลองอีกครั้ง",
        "Cannot connect to Pearnly. Check the network and try again.",
        "Pearnly に接続できません。ネットワークを確認して、もう一度お試しください。",
    ),
    "pair_err_unexpected": (
        "操作未完成，请重试。",
        "ดำเนินการไม่สำเร็จ กรุณาลองใหม่",
        "Not completed. Try again.",
        "完了しませんでした。もう一度お試しください。",
    ),
    "pair_err_rejected": ("云端校验未通过", "การตรวจสอบจากคลาวด์ไม่ผ่าน", "Cloud verification failed", "クラウドでの確認に失敗しました"),
    "pair_err_profile_save": (
        "无法保存连接，请重试。",
        "ไม่สามารถบันทึกการเชื่อมต่อได้ กรุณาลองใหม่",
        "Could not save the connection. Try again.",
        "接続を保存できません。もう一度お試しください。",
    ),
    # 多账套管理
    # 主窗口（2026-10-08 起连接、文件夹、开机启动都在这一个窗口里；托盘左键或「打开小助手」打开）。
    "profiles_title": ("CC", "CC", "CC", "CC"),
    "profiles_empty": (
        "暂无成员。请在网页「连接 Express」中复制配对码，然后点击「添加成员」。",
        "ยังไม่มีสมาชิก กรุณาคัดลอกรหัสจับคู่จากหน้า «เชื่อมต่อ Express» บนเว็บ แล้วกด «เพิ่มสมาชิก»",
        "No members. Copy the pairing code from “Connect Express” on the website, then click “Add member”.",
        "メンバーはいません。ウェブの「Express に接続」でペアリングコードをコピーし、「メンバーを追加」をクリックしてください。",
    ),
    "profiles_active": ("已启用", "ใช้งานอยู่", "Active", "有効"),
    "profiles_paused": ("已暂停", "พักการใช้งาน", "Paused", "一時停止中"),
    "profiles_connecting": ("正在连接 Pearnly", "กำลังเชื่อมต่อ Pearnly", "Connecting to Pearnly", "Pearnly に接続しています"),
    "profiles_online": ("Pearnly 已连接", "เชื่อมต่อ Pearnly แล้ว", "Pearnly connected", "Pearnly に接続済み"),
    "profiles_processing": ("处理中", "กำลังประมวลผล", "Processing", "処理中"),
    "profiles_attention": ("需处理", "ต้องดำเนินการ", "Needs attention", "要対応"),
    "profiles_offline": ("Pearnly 连接中断", "ขาดการเชื่อมต่อกับ Pearnly", "Pearnly disconnected", "Pearnly との接続が切れています"),
    "profiles_add": ("添加成员", "เพิ่มสมาชิก", "Add member", "メンバーを追加"),
    "profiles_edit": ("更换配对码…", "เปลี่ยนรหัสจับคู่…", "Change pairing code…", "ペアリングコードを変更…"),
    "profiles_pause": ("暂停", "พักการใช้งาน", "Pause", "一時停止"),
    "profiles_resume": ("恢复", "กลับมาใช้งาน", "Resume", "再開"),
    "profiles_remove": ("移除", "นำออก", "Remove", "削除"),
    "profiles_remove_title": ("移除成员", "นำสมาชิกออก", "Remove member", "メンバーを削除"),
    "profiles_remove_body": (
        "确定要移除成员「{company}」吗？Express 中的数据不会被删除。",
        "ต้องการนำสมาชิก «{company}» ออกใช่หรือไม่ ข้อมูลใน Express จะไม่ถูกลบ",
        "Remove member “{company}”? Data in Express will not be deleted.",
        "メンバー「{company}」を削除しますか？Express のデータは削除されません。",
    ),
    "profiles_error": (
        "无法更新连接，请重试。",
        "ไม่สามารถอัปเดตการเชื่อมต่อได้ กรุณาลองใหม่",
        "Could not update the connection. Try again.",
        "接続を更新できません。もう一度お試しください。",
    ),
    # 托盘
    "tray_tooltip": ("CC", "CC", "CC", "CC"),
    # Tomo 桌面窗口（gui_tomo.py）
    "tomo_open": ("打开 CC", "เปิด CC", "Open CC", "CC を開く"),
    "tomo_new_chat": ("新对话", "แชทใหม่", "New chat", "新しい会話"),
    "tomo_ask_placeholder": ("问问 CC", "ถาม CC", "Ask CC", "CC に質問"),
    "tomo_still_running": (
        "CC 仍在后台运行，可从任务栏右下角打开。",
        "CC ยังทำงานอยู่เบื้องหลัง เปิดได้จากมุมขวาล่างของแถบงาน",
        "CC is still running in the background. Open it from the bottom right of the taskbar.",
        "CC はバックグラウンドで実行中です。タスクバーの右下から開けます。",
    ),
    "tomo_downloaded": ("已下载：{name}", "ดาวน์โหลดแล้ว: {name}", "Downloaded: {name}", "ダウンロードしました：{name}"),
    "tray_no_connection": ("未连接 Express", "ยังไม่ได้เชื่อมต่อ Express", "Express not connected", "Express 未接続"),
    "menu_file": ("文件", "ไฟล์", "File", "ファイル"),
    "menu_view": ("视图", "มุมมอง", "View", "表示"),
    "menu_reload": ("重新加载", "โหลดใหม่", "Reload", "再読み込み"),
    "menu_zoom_in": ("放大", "ขยาย", "Zoom in", "拡大"),
    "menu_zoom_out": ("缩小", "ย่อ", "Zoom out", "縮小"),
    "menu_zoom_reset": ("实际大小", "ขนาดจริง", "Actual size", "実際のサイズ"),
    "tray_starting": ("启动中…", "กำลังเริ่ม…", "Starting…", "起動しています…"),
    "tray_open_logs": ("打开日志文件夹", "เปิดโฟลเดอร์ไฟล์บันทึกการทำงาน", "Open log folder", "ログフォルダーを開く"),
    # 定时任务（原「自动录入」，2026-10-09 改名）：本机只设网页可选的根目录（安全边界），其余在网页「定时任务」里设。
    "folders_title": ("定时任务文件夹", "โฟลเดอร์งานตามกำหนดเวลา", "Scheduled task folders", "定期タスクのフォルダー"),
    "folders_label": ("网页可选的文件夹", "โฟลเดอร์ที่เลือกได้บนเว็บ", "Folders the website can choose", "ウェブで選択できるフォルダー"),
    "folders_add": ("添加文件夹…", "เพิ่มโฟลเดอร์…", "Add folder…", "フォルダーを追加…"),
    "folders_remove": ("移除", "นำออก", "Remove", "削除"),
    "folders_save": ("保存", "บันทึก", "Save", "保存"),
    "folders_pick": (
        "选择允许读取的文件夹",
        "เลือกโฟลเดอร์ที่อนุญาตให้อ่าน",
        "Choose a folder to allow reading",
        "読み取りを許可するフォルダーを選択",
    ),
    "folders_program": (
        "不能选择 CC 的程序文件夹。",
        "เลือกโฟลเดอร์โปรแกรมของ CC ไม่ได้",
        "The CC program folder cannot be chosen.",
        "CC のプログラムフォルダーは選択できません。",
    ),
    # 公司的「选择文件夹…」（2026-10-15，CC 按公司分组；pick_folder 的失败码）
    "folders_contains": (
        "该文件夹包含已授权的文件夹 {path}，请选择某一家公司的文件夹。",
        "โฟลเดอร์นี้มีโฟลเดอร์ที่อนุญาตแล้ว {path} กรุณาเลือกโฟลเดอร์ของบริษัทเดียว",
        "This folder contains the authorized folder {path}. Choose the folder of one company.",
        "このフォルダーには許可済みのフォルダー {path} が含まれています。1 社のフォルダーを選択してください。",
    ),
    "folders_too_broad": (
        "不能选择整个磁盘或系统文件夹。",
        "เลือกทั้งไดรฟ์หรือโฟลเดอร์ระบบไม่ได้",
        "A whole drive or a system folder cannot be chosen.",
        "ドライブ全体やシステムフォルダーは選択できません。",
    ),
    "folders_full": (
        "此电脑授权的文件夹已达上限。",
        "เครื่องนี้มีโฟลเดอร์ครบจำนวนสูงสุดแล้ว",
        "This computer has reached the limit of authorized folders.",
        "このパソコンで許可できるフォルダーの上限に達しました。",
    ),
    "folders_not_connected": (
        "请先连接此电脑。",
        "กรุณาเชื่อมต่อเครื่องนี้ก่อน",
        "Connect this computer first.",
        "先にこのパソコンを接続してください。",
    ),
    "folders_other_site": (
        "此电脑连接的是另一家公司。",
        "เครื่องนี้เชื่อมต่อกับบริษัทอื่น",
        "This computer is connected to another company.",
        "このパソコンは別の会社に接続されています。",
    ),
    "folders_overlap": (
        "该文件夹已被其他连接使用或与其存在包含关系：{path}",
        "โฟลเดอร์นี้ถูกใช้กับการเชื่อมต่ออื่นแล้ว หรือซ้อนทับกับโฟลเดอร์ของการเชื่อมต่ออื่น: {path}",
        "This folder is used by another connection or overlaps with it: {path}",
        "このフォルダーは別の接続で使用中か、その接続と重なっています：{path}",
    ),
    "log_folder_run": (
        "开始定时任务：{name}",
        "เริ่มงานตามกำหนดเวลา: {name}",
        "Scheduled task started: {name}",
        "定期タスクを開始しました：{name}",
    ),
    "log_folder_done": (
        "定时任务已上传：{name} · 新文件 {n} 份，失败 {fail} 份",
        "งานตามกำหนดเวลา อัปโหลดแล้ว: {name} · ไฟล์ใหม่ {n} ไฟล์ ไม่สำเร็จ {fail} ไฟล์",
        "Scheduled task uploaded: {name} · {n} new files, {fail} failed",
        "定期タスクをアップロードしました：{name} · 新しいファイル {n} 件、失敗 {fail} 件",
    ),
    "tray_quit": ("退出", "ออกจากโปรแกรม", "Quit", "終了"),
    "st_idle": ("空闲", "พร้อมทำงาน", "Idle", "待機中"),
    "st_working": ("正在录入", "กำลังบันทึก", "Entering", "入力中"),
    "st_indexing": ("正在更新索引…", "กำลังอัปเดตดัชนี…", "Updating index…", "インデックスを更新しています…"),
    "st_waiting_lock": (
        "正在等待 Express 释放账套…",
        "กำลังรอให้ Express ปล่อยชุดบัญชี…",
        "Waiting for Express to release the account set…",
        "Express が会計データを解放するのを待っています…",
    ),
    # 账套被占用弹窗(写盘前探测到独占拿不到 → 原生弹窗交用户:重试 / 终止整批)
    "busy_title": ("账套正被占用", "ชุดบัญชีกำลังถูกใช้งาน", "Account set in use", "会計データが使用中です"),
    "busy_body": (
        "账套正被 Express 占用。关闭正在使用该账套的 Express 后点击「重试」，或点击「终止推送」。",
        "ชุดบัญชีกำลังถูกใช้งานใน Express ปิด Express ที่ใช้ชุดบัญชีนี้แล้วกด「ลองใหม่」 หรือกด「ยกเลิกการส่ง」",
        "The account set is in use by Express. Close the Express using it, then click “Retry”, or click “Stop push”.",
        "会計データは Express で使用中です。この会計データを使用している Express を閉じてから「再試行」をクリックするか、「送信を中止」をクリックしてください。",
    ),
    "busy_retry": ("重试", "ลองใหม่", "Retry", "再試行"),
    "busy_abort": ("终止推送", "ยกเลิกการส่ง", "Stop push", "送信を中止"),
    "log_busy_aborted": (
        "账套被占用，已终止本批推送。",
        "ชุดบัญชีถูกใช้งานอยู่ ยกเลิกการส่งชุดนี้แล้ว",
        "The account set was in use. This batch push was stopped.",
        "会計データが使用中のため、今回の一括送信を中止しました。",
    ),
    "st_error": ("出错", "เกิดข้อผิดพลาด", "Error", "エラー"),
    "st_offline": ("离线", "ออฟไลน์", "Offline", "オフライン"),
    "st_connected": ("已连接", "เชื่อมต่อแล้ว", "Connected", "接続済み"),
    "st_stalled_giveup": (
        "连接异常，请检查账套路径或网络后重新启动 CC",
        "การเชื่อมต่อผิดปกติ กรุณาตรวจสอบตำแหน่งชุดบัญชีหรือเครือข่าย แล้วเริ่ม CC ใหม่",
        "Connection problem. Check the account set path or network, then restart CC",
        "接続に問題があります。会計データのパスまたはネットワークを確認して、CC を再起動してください",
    ),
    # 托盘只留「打开小助手」一个入口（连接、文件夹、开机启动都在主窗口）。
    "tray_open": ("连接与设置", "การเชื่อมต่อและการตั้งค่า", "Connections and settings", "接続と設定"),
    # 离线友好原因(替代英文异常类名 HTTPStatusError):
    # 密钥被拒（电脑被移除、成员被收窄等）：1.7.0 起没有配对码，回到设置点「连接」（第 7 批 C10 ②，10-18）
    "off_auth": (
        "连接已失效，请在设置中重新连接",
        "การเชื่อมต่อใช้งานไม่ได้แล้ว กรุณาเชื่อมต่อใหม่ในการตั้งค่า",
        "The connection has expired. Reconnect in Settings",
        "接続の有効期限が切れました。設定で再接続してください",
    ),
    "off_disabled": (
        "此连接已被停用",
        "การเชื่อมต่อนี้ถูกปิดใช้งานแล้ว",
        "This connection has been disabled",
        "この接続は無効になっています",
    ),
    "off_push_off": (
        "云端推送未开启",
        "ยังไม่ได้เปิดการส่งข้อมูลบนคลาวด์",
        "Cloud push is not enabled",
        "クラウドへの送信が有効になっていません",
    ),
    "off_network": (
        "无法连接 Pearnly，请检查网络",
        "ไม่สามารถเชื่อมต่อ Pearnly ได้ กรุณาตรวจสอบเครือข่าย",
        "Cannot connect to Pearnly. Check the network",
        "Pearnly に接続できません。ネットワークを確認してください",
    ),
    # 活动记录(activity.log · 客户「打开日志」看的人话事件):
    "log_started": ("CC 已启动。", "CC เริ่มทำงานแล้ว", "CC started.", "CC を起動しました。"),
    "log_connected": (
        "已连接 Pearnly，等待新发票。",
        "เชื่อมต่อ Pearnly แล้ว กำลังรอใบกำกับภาษีใหม่",
        "Connected to Pearnly. Waiting for new invoices.",
        "Pearnly に接続しました。新しい請求書を待っています。",
    ),
    "log_offline": (
        "与 Pearnly 的连接已断开：{reason}",
        "ขาดการเชื่อมต่อกับ Pearnly: {reason}",
        "Disconnected from Pearnly: {reason}",
        "Pearnly との接続が切れました：{reason}",
    ),
    "log_self_healed": ("CC 已自动重启。", "CC รีสตาร์ทอัตโนมัติแล้ว", "CC restarted automatically.", "CC を自動で再起動しました。"),
    "log_recorded": (
        "已录入 1 张发票（{inv}）。",
        "บันทึกใบกำกับภาษีแล้ว 1 ใบ ({inv})",
        "Entered 1 invoice ({inv}).",
        "請求書を 1 件入力しました（{inv}）。",
    ),
    "log_record_fail": (
        "发票录入失败：{err}",
        "บันทึกใบกำกับภาษีไม่สำเร็จ: {err}",
        "Invoice entry failed: {err}",
        "請求書を入力できませんでした：{err}",
    ),
    # 连上 Pearnly（池里的电脑不属于某一份账套，不再写账套名；第 7 批 C11，10-18）
    "log_paired": ("已连接 Pearnly。", "เชื่อมต่อ Pearnly แล้ว", "Connected to Pearnly.", "Pearnly に接続しました。"),
    "log_quit": ("CC 已退出。", "CC ปิดการทำงานแล้ว", "CC quit.", "CC を終了しました。"),
    # 自动更新
    "tray_update_to": (
        "更新到最新版 v{ver}",
        "อัปเดตเป็นเวอร์ชันล่าสุด v{ver}",
        "Update to the latest version v{ver}",
        "最新バージョン v{ver} に更新",
    ),
    # 录入并重建索引完成(每张写盘后即重建索引 · 真可查)→ 系统原生气泡
    "notif_recorded_title": ("已录入 Express", "บันทึกเข้า Express แล้ว", "Entered in Express", "Express に入力済み"),
    "notif_recorded_one": (
        "{inv} 已录入 Express。",
        "{inv} บันทึกเข้า Express แล้ว",
        "{inv} entered in Express.",
        "{inv} を Express に入力しました。",
    ),
    "notif_recorded_many": (
        "本批 {n} 张已录入 Express。",
        "บันทึกเข้า Express แล้ว {n} ใบในชุดนี้",
        "{n} documents in this batch entered in Express.",
        "今回の {n} 件を Express に入力しました。",
    ),
    # 本批有失败 → 同样弹原生通知(诚实反馈·不拿失败冒充成功)
    "notif_partial_title": ("本批部分录入", "ชุดนี้บันทึกได้บางส่วน", "Batch partly entered", "今回の一部を入力しました"),
    "notif_partial_body": (
        "本批 {ok} 张已录入，{fail} 张录入失败，请在网页中处理。",
        "ชุดนี้บันทึกแล้ว {ok} ใบ ไม่สำเร็จ {fail} ใบ กรุณาดำเนินการบนเว็บ",
        "{ok} entered and {fail} failed in this batch. Handle them on the website.",
        "今回 {ok} 件を入力し、{fail} 件は入力できませんでした。ウェブで処理してください。",
    ),
    "notif_failed_title": ("本批录入失败", "ชุดนี้บันทึกไม่สำเร็จ", "Batch entry failed", "今回の入力に失敗しました"),
    "notif_failed_body": (
        "本批 {fail} 张录入失败，请在网页日志中查看原因。",
        "ชุดนี้บันทึกไม่สำเร็จ {fail} ใบ กรุณาดูสาเหตุในบันทึกการทำงานบนเว็บ",
        "{fail} in this batch failed. See the reason in the website log.",
        "今回 {fail} 件を入力できませんでした。理由はウェブのログで確認してください。",
    ),
    "update_avail_title": ("有新版本", "มีเวอร์ชันใหม่", "New version available", "新しいバージョンがあります"),
    "update_avail_body": (
        "CC v{ver} 已发布，当前录入完成后将自动更新并重启。",
        "CC v{ver} พร้อมใช้งานแล้ว ระบบจะอัปเดตและรีสตาร์ทอัตโนมัติเมื่อบันทึกรายการปัจจุบันเสร็จ",
        "CC v{ver} is available. CC will update and restart after the current entry finishes.",
        "CC v{ver} が公開されました。現在の入力が終わると自動で更新して再起動します。",
    ),
    "update_downloading": (
        "正在下载更新，完成后将自动重启…",
        "กำลังดาวน์โหลดอัปเดต ระบบจะรีสตาร์ทอัตโนมัติเมื่อเสร็จ…",
        "Downloading the update. CC will restart when it finishes…",
        "更新をダウンロードしています。完了すると自動で再起動します…",
    ),
    "update_failed": (
        "更新失败，请稍后重试或从网页重新下载安装。",
        "อัปเดตไม่สำเร็จ กรุณาลองใหม่ภายหลัง หรือดาวน์โหลดและติดตั้งใหม่จากเว็บ",
        "Update failed. Try again later or download and install again from the website.",
        "更新に失敗しました。しばらくしてからもう一度お試しいただくか、ウェブから再度ダウンロードしてインストールしてください。",
    ),
    # 主窗口底部的更新按钮与状态（2026-10-08：发布后一分钟内发现新版，按钮可立即检查 / 立即更新）。
    "upd_check": ("检查更新", "ตรวจสอบอัปเดต", "Check for updates", "アップデートを確認"),
    "upd_now": ("立即更新", "อัปเดตทันที", "Update now", "今すぐ更新"),
    "upd_checking": ("正在检查…", "กำลังตรวจสอบ…", "Checking…", "確認しています…"),
    "upd_latest": ("已是最新版本", "เป็นเวอร์ชันล่าสุดแล้ว", "Up to date", "最新バージョンです"),
    "upd_found": ("发现新版本 v{ver}", "พบเวอร์ชันใหม่ v{ver}", "New version v{ver}", "新しいバージョン v{ver}"),
    "upd_downloading": ("正在下载 v{ver}…", "กำลังดาวน์โหลด v{ver}…", "Downloading v{ver}…", "v{ver} をダウンロードしています…"),
    "upd_ready": (
        "v{ver} 已下载，当前录入完成后将自动安装",
        "ดาวน์โหลด v{ver} แล้ว จะติดตั้งอัตโนมัติเมื่อบันทึกรายการปัจจุบันเสร็จ",
        "v{ver} downloaded. It will install after the current entry finishes",
        "v{ver} をダウンロードしました。現在の入力が終わると自動でインストールします",
    ),
    "upd_installing": (
        "正在安装 v{ver}，CC 将自动重启",
        "กำลังติดตั้ง v{ver} CC จะรีสตาร์ทอัตโนมัติ",
        "Installing v{ver}. CC will restart",
        "v{ver} をインストールしています。CC は自動で再起動します",
    ),
    "upd_failed": (
        "v{ver} 下载失败，稍后将自动重试",
        "ดาวน์โหลด v{ver} ไม่สำเร็จ ระบบจะลองใหม่อัตโนมัติภายหลัง",
        "Download of v{ver} failed. CC will retry later",
        "v{ver} のダウンロードに失敗しました。後で自動で再試行します",
    ),
    # 自动更新被 Windows 拦下（update_block.py）：一行 +「下载新版」，打开官网下载
    "update_blocked": (
        "Windows 拦截了 v{ver} 的自动更新",
        "Windows บล็อกการอัปเดตอัตโนมัติเป็น v{ver}",
        "Windows blocked the automatic update to v{ver}",
        "Windows が v{ver} への自動更新をブロックしました",
    ),
    "upd_download": ("下载新版", "ดาวน์โหลดเวอร์ชันใหม่", "Download new version", "新しいバージョンをダウンロード"),
    "tray_download_to": (
        "下载新版 v{ver}",
        "ดาวน์โหลดเวอร์ชันใหม่ v{ver}",
        "Download new version v{ver}",
        "新しいバージョン v{ver} をダウンロード",
    ),
    "log_update_blocked": (
        "Windows 拦截了 v{ver} 的自动更新。",
        "Windows บล็อกการอัปเดตอัตโนมัติเป็น v{ver}",
        "Windows blocked the automatic update to v{ver}.",
        "Windows が v{ver} への自動更新をブロックしました。",
    ),
    "log_update_found": (
        "发现新版本 v{ver}。",
        "พบเวอร์ชันใหม่ v{ver}",
        "New version v{ver} found.",
        "新しいバージョン v{ver} が見つかりました。",
    ),
    "log_updating": ("正在更新到 v{ver}…", "กำลังอัปเดตเป็น v{ver}…", "Updating to v{ver}…", "v{ver} に更新しています…"),
}


# _M 每条的列:(zh, th, en, ja)
_COLUMN = {"zh": 0, "th": 1, "en": 2, "ja": 3}


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
    """按 Windows 用户界面语言:泰→th / 中→zh / 英→en / 日→ja / 其它→th(客户多为泰语)。"""
    try:
        import ctypes

        primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        if primary == 0x04:  # 中文
            return "zh"
        if primary == 0x1E:  # 泰语
            return "th"
        if primary == 0x09:  # 英语
            return "en"
        if primary == 0x11:  # 日语
            return "ja"
    except Exception:
        pass
    return _DEFAULT


def normalize(lang: str) -> str:
    lang = (lang or "").strip().lower()
    return lang if lang in LANGS else default_lang()


def t(key: str, lang: str, **fmt) -> str:
    """取 key 的 lang 文案(缺 lang 用默认);带占位则按 fmt 格式化。"""
    texts = _M.get(key)
    if not texts:
        return key
    s = texts[_COLUMN[normalize(lang)]]
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
