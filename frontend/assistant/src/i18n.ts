// 对话页的文案（语言跟站点：www/assistant 把本次请求的语言写进 <html lang>）。官方、简短；不加说明性文字。

const MESSAGES = {
  en: {
    language: "Language",
    help: "Get help",
    learnMore: "Learn more",
    about: "About us",
    privacy: "Privacy policy",
    terms: "Terms of use",
    starred: "Starred",
    star: "Star",
    unstar: "Unstar",
    rename: "Rename",
    logout: "Log out",
    files: "Files",
    preview: "Preview",
    source: "Code",
    download: "Download",
    close: "Close",
    noPreview: "Preview not available",
    attach: "Add files",
    removeFile: "Remove",
    uploadFailed: "Upload failed",
    allow: "Allow",
    deny: "Deny",
    allowed: "Allowed",
    denied: "Denied",
    tools: "Tools",
    newChat: "New chat",
    search: "Search",
    today: "Today",
    yesterday: "Yesterday",
    untitled: "New chat",
    noResults: "No results",
    archive: "Archive",
    delete: "Delete",
    more: "More",
    placeholder: "How can I help you today?",
    send: "Send",
    stop: "Stop",
    copy: "Copy",
    retry: "Retry",
    edit: "Edit",
    previous: "Previous",
    next: "Next",
    scrollToBottom: "Scroll to bottom",
    reconnecting: "Reconnecting…",
    morning: "Good morning",
    afternoon: "Good afternoon",
    evening: "Good evening",
    sidebar: "Toggle sidebar",
  },
  zh: {
    language: "语言",
    help: "获取帮助",
    learnMore: "了解更多",
    about: "关于我们",
    privacy: "隐私政策",
    terms: "使用条款",
    starred: "已加星标",
    star: "加星标",
    unstar: "取消星标",
    rename: "重命名",
    logout: "退出登录",
    files: "文件",
    preview: "预览",
    source: "代码",
    download: "下载",
    close: "关闭",
    noPreview: "不支持预览",
    attach: "添加文件",
    removeFile: "移除",
    uploadFailed: "上传失败",
    allow: "允许",
    deny: "拒绝",
    allowed: "已允许",
    denied: "已拒绝",
    tools: "工具",
    newChat: "新建",
    search: "搜索",
    today: "今天",
    yesterday: "昨天",
    untitled: "新对话",
    noResults: "没有结果",
    archive: "归档",
    delete: "删除",
    more: "更多",
    placeholder: "今天我能为您做些什么？",
    send: "发送",
    stop: "停止",
    copy: "复制",
    retry: "重试",
    edit: "编辑",
    previous: "上一个",
    next: "下一个",
    scrollToBottom: "滚动到底部",
    reconnecting: "正在重新连接…",
    morning: "早上好",
    afternoon: "下午好",
    evening: "晚上好",
    sidebar: "切换侧栏",
  },
  th: {
    language: "ภาษา",
    help: "ขอความช่วยเหลือ",
    learnMore: "ดูข้อมูลเพิ่มเติม",
    about: "เกี่ยวกับเรา",
    privacy: "นโยบายความเป็นส่วนตัว",
    terms: "ข้อกำหนดการใช้งาน",
    starred: "ติดดาว",
    star: "ติดดาว",
    unstar: "เลิกติดดาว",
    rename: "เปลี่ยนชื่อ",
    logout: "ออกจากระบบ",
    files: "ไฟล์",
    preview: "ตัวอย่าง",
    source: "โค้ด",
    download: "ดาวน์โหลด",
    close: "ปิด",
    noPreview: "ไม่รองรับการแสดงตัวอย่าง",
    attach: "เพิ่มไฟล์",
    removeFile: "นำออก",
    uploadFailed: "อัปโหลดไม่สำเร็จ",
    allow: "อนุญาต",
    deny: "ปฏิเสธ",
    allowed: "อนุญาตแล้ว",
    denied: "ปฏิเสธแล้ว",
    tools: "เครื่องมือ",
    newChat: "สร้างใหม่",
    search: "ค้นหา",
    today: "วันนี้",
    yesterday: "เมื่อวาน",
    untitled: "แชทใหม่",
    noResults: "ไม่พบผลลัพธ์",
    archive: "เก็บถาวร",
    delete: "ลบ",
    more: "เพิ่มเติม",
    placeholder: "วันนี้ต้องการให้ช่วยอะไร",
    send: "ส่ง",
    stop: "หยุด",
    copy: "คัดลอก",
    retry: "ลองใหม่",
    edit: "แก้ไข",
    previous: "ก่อนหน้า",
    next: "ถัดไป",
    scrollToBottom: "เลื่อนลงล่างสุด",
    reconnecting: "กำลังเชื่อมต่อใหม่…",
    morning: "สวัสดีตอนเช้า",
    afternoon: "สวัสดีตอนบ่าย",
    evening: "สวัสดีตอนเย็น",
    sidebar: "เปิด/ปิดแถบด้านข้าง",
  },
} as const;

type Key = keyof (typeof MESSAGES)["en"];

export const lang = (document.documentElement.lang || "th").slice(0, 2);

export function t(key: Key): string {
  return ((MESSAGES as Record<string, Record<Key, string>>)[lang] ?? MESSAGES.th)[key];
}

/** 历史分组标题里的日期：按站点语言显示「10月8日」这类。 */
export function dayLabel(date: Date): string {
  return new Intl.DateTimeFormat(lang, { month: "long", day: "numeric" }).format(date);
}

export function greeting(now = new Date()): string {
  const h = now.getHours();
  return t(h < 12 ? "morning" : h < 18 ? "afternoon" : "evening");
}

// 工具步骤的说明（同 claude.ai：灰色一行，进行中 / 完成 / 未执行三种说法）。
const STEPS = {
  en: {
    thinking: "Thinking…", thought: "Thought process", usedTool: "Used a tool", cancelled: "Cancelled", error: "Error",
    showOutput: "Show output", hideOutput: "Hide output",
    bash: ["Running a command", "Ran a command", "Run a command"],
    read: ["Reading", "Read", "Read file"], write: ["Writing", "Wrote", "Write file"], edit: ["Editing", "Edited", "Edit file"],
    glob: ["Finding files", "Found files", "Find files"], grep: ["Searching files", "Searched files", "Search files"],
    search: ["Searching the web", "Searched the web", "Search the web"], fetch: ["Opening", "Opened", "Open page"], failed: "Failed",
    steps: (n: number) => `${n} ${n === 1 ? "step" : "steps"}`, lines: (n: number) => `${n} ${n === 1 ? "line" : "lines"}`,
    matches: (n: number) => `${n} ${n === 1 ? "match" : "matches"}`,
  },
  zh: {
    thinking: "正在思考…", thought: "思考过程", usedTool: "已使用工具", cancelled: "已取消", error: "错误",
    showOutput: "显示输出", hideOutput: "收起输出",
    bash: ["正在运行命令", "已运行命令", "运行命令"],
    read: ["正在读取", "已读取", "读取文件"], write: ["正在写入", "已写入", "写入文件"], edit: ["正在编辑", "已编辑", "编辑文件"],
    glob: ["正在查找文件", "已查找文件", "查找文件"], grep: ["正在搜索文件内容", "已搜索文件内容", "搜索文件内容"],
    search: ["正在搜索网页", "已搜索网页", "搜索网页"], fetch: ["正在打开", "已打开", "打开网页"], failed: "失败",
    steps: (n: number) => `${n} 个步骤`, lines: (n: number) => `${n} 行`, matches: (n: number) => `${n} 处匹配`,
  },
  th: {
    thinking: "กำลังคิด…", thought: "กระบวนการคิด", usedTool: "ใช้เครื่องมือแล้ว", cancelled: "ยกเลิกแล้ว", error: "ข้อผิดพลาด",
    showOutput: "แสดงผลลัพธ์", hideOutput: "ซ่อนผลลัพธ์",
    bash: ["กำลังรันคำสั่ง", "รันคำสั่งแล้ว", "รันคำสั่ง"],
    read: ["กำลังอ่าน", "อ่านแล้ว", "อ่านไฟล์"], write: ["กำลังเขียน", "เขียนแล้ว", "เขียนไฟล์"], edit: ["กำลังแก้ไข", "แก้ไขแล้ว", "แก้ไขไฟล์"],
    glob: ["กำลังค้นหาไฟล์", "ค้นหาไฟล์แล้ว", "ค้นหาไฟล์"], grep: ["กำลังค้นหาในไฟล์", "ค้นหาในไฟล์แล้ว", "ค้นหาในไฟล์"],
    search: ["กำลังค้นหาเว็บ", "ค้นหาเว็บแล้ว", "ค้นหาเว็บ"], fetch: ["กำลังเปิด", "เปิดแล้ว", "เปิดหน้าเว็บ"], failed: "ไม่สำเร็จ",
    steps: (n: number) => `${n} ขั้นตอน`, lines: (n: number) => `${n} บรรทัด`, matches: (n: number) => `${n} รายการที่ตรง`,
  },
};

export const steps = (STEPS as Record<string, (typeof STEPS)["en"]>)[lang] ?? STEPS.th;

// 宣传页（pearnly.com）上已有的页面：语言前缀与路径同 website/content.py。
const SITE = "https://pearnly.com";
const SITE_PREFIX: Record<string, string> = { th: "", en: "/en", zh: "/zh" };
export function siteUrl(path: string): string {
  return `${SITE}${SITE_PREFIX[lang] ?? ""}${path}`;
}
export const HELP_URL = `${SITE}/guides/`; // 使用指南目前只有泰文版

/** 对话页可选的语言（与站点账户语言一致，切换走 pearnly_core.account.preferences.set_language）。 */
export const LANGUAGES = [
  { code: "zh", label: "中文" },
  { code: "th", label: "ไทย" },
  { code: "en", label: "English" },
];
