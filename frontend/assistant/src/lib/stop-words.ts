// 「说停就停」（A 线，主会话 11-10：「一句话下令开始」的第三道保险）：回复进行中，用户发来的整条消息只是停止词，就当按了停止键
// （managed-agents/send-queue.ts），不进排队、不发出；带别的内容照旧排队。四种语言一张表；比较前去掉空格与标点、英文不分大小写。
// 只认这张表里的整句，不猜意思（「停一下再看第 3 张」是一句话，照旧排队）。

const WORDS: readonly string[] = [
  // 中文
  "停", "停下", "停止", "停下来", "先停", "别做了", "不要做了",
  // English
  "stop", "stop it", "halt", "cancel",
  // ไทย
  "หยุด", "หยุดก่อน", "หยุดเลย", "พอแล้ว",
  // 日本語
  "止めて", "やめて", "停止", "ストップ", "止まって",
];

const squeeze = (text: string) => text.normalize("NFKC").toLocaleLowerCase().replace(/[\s\p{P}\p{S}]+/gu, "");

const STOP = new Set(WORDS.map(squeeze));

/** 这句话是不是只是一个停止词（去掉空格与标点后与词表里某一个完全一样）。 */
export function isStopWord(text: string): boolean {
  const word = squeeze(text);
  return word !== "" && STOP.has(word);
}
