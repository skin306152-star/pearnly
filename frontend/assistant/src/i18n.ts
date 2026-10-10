// 对话页外壳的文案（语言跟站点：www/assistant 把本次请求的语言写进 <html lang>）。
// 只放必要的几条；产品名待定，先用 Pearnly。

type Key = "title" | "reconnecting";

const MESSAGES: Record<string, Record<Key, string>> = {
  en: { title: "Pearnly", reconnecting: "Reconnecting…" },
  zh: { title: "Pearnly", reconnecting: "正在重新连接…" },
  th: { title: "Pearnly", reconnecting: "กำลังเชื่อมต่อใหม่…" },
};

const lang = (document.documentElement.lang || "th").slice(0, 2);

export function t(key: Key): string {
  return (MESSAGES[lang] ?? MESSAGES.th)[key];
}
