// 对话页自己的文案（语言跟站点：www/assistant 把本次请求的语言写进 <html lang>）。
// 第一版只有外壳几句；assistant-ui 组件里的按钮提示仍是英文，列为后续批次（见工作记录）。

type Key = "title" | "welcome" | "transient" | "reconnecting";

const MESSAGES: Record<string, Record<Key, string>> = {
  en: {
    title: "Pearnly Assistant",
    welcome: "Tell me what you need done.",
    transient: "Temporary error, retrying:",
    reconnecting: "Reconnecting…",
  },
  zh: {
    title: "Pearnly 助手",
    welcome: "说说你要办什么。",
    transient: "临时出错，正在重试：",
    reconnecting: "正在重新连接…",
  },
  th: {
    title: "ผู้ช่วย Pearnly",
    welcome: "บอกได้เลยว่าต้องการให้ช่วยอะไร",
    transient: "เกิดข้อผิดพลาดชั่วคราว กำลังลองใหม่:",
    reconnecting: "กำลังเชื่อมต่อใหม่…",
  },
};

const lang = (document.documentElement.lang || "th").slice(0, 2);

export function t(key: Key): string {
  return (MESSAGES[lang] ?? MESSAGES.th)[key];
}
