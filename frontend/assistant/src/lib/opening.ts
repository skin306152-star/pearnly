// 「正在打开」闸（主会话 11-17，流程 cc.deep-link-attach）：链接 / 通知要打开某段对话（lib/nav.ts 的 useDeepLinks）时，切到那里之前
// 输入框不可用——加附件、发送都不会落进组件库先给的「新对话」（以前会当场建出一段新会话，附件和这句进了那段，切过去后从眼前消失）。
// token 非 0 = 有一次打开在途。页面带着要打开的地址加载时，模块一加载就关上（第一次渲染之前）。超过 OPEN_GATE_MS 由 nav 放开并落到
// 「找不到」，同时作废这一次：一次只认最新的 token，晚到的响应看到自己已不是在途的那次就不再切走页面。
// 只依赖 zustand：运行时（runtime-provider）、附件（attachments）都从这里读，不引 nav（免得循环引用）。

import { create } from "zustand";
import { reportCaught } from "./caught";

export const OPEN_GATE_MS = 30_000;

const KEYS = ["c", "view", "company", "task"];

/** 地址里带着要打开的东西（同 nav.targetOf 认的四个参数，只在对话页）。 */
export function linkedAt(href: string): boolean {
  try {
    const url = new URL(href);
    return url.pathname.startsWith("/assistant") && KEYS.some((key) => !!url.searchParams.get(key));
  } catch (caughtError) { reportCaught(caughtError, "nav");
    return false;
  }
}

let seq = 0;
export const useOpening = create<{ token: number }>(() => ({
  token: typeof location !== "undefined" && linkedAt(location.href) ? -1 : 0,
}));

/** 开始一次打开（作废之前还在途的那次），回这次的 token。 */
export function beginOpening(): number {
  seq += 1;
  useOpening.setState({ token: seq });
  return seq;
}

/** 这次打开还是不是在途的那次。 */
export function isOpening(token: number): boolean {
  return token !== 0 && useOpening.getState().token === token;
}

/** 结束这次打开（只结束自己：已被更新的一次取代的不动）。 */
export function endOpening(token: number): void {
  if (isOpening(token)) useOpening.setState({ token: 0 });
}

/** 此刻有没有打开在途（输入框不可用）。 */
export const opening = (): boolean => useOpening.getState().token !== 0;

/** 闸住打开一次：open 拿到 live（这次是否还作数，alive 为假或已被更新的一次 / 超时作废就是假），每次要切页面、改状态前先看它；
 *  超过 ms 还没打开完就放开、作废这一次并调 onTimeout（落到「找不到」）。回 open 的结果。 */
export async function gatedOpen(open: (live: () => boolean) => Promise<void>, onTimeout: () => void,
                                alive: () => boolean = () => true, ms: number = OPEN_GATE_MS): Promise<void> {
  const token = beginOpening();
  const live = () => alive() && isOpening(token);
  const timer = setTimeout(() => {
    if (!isOpening(token)) return;
    endOpening(token);
    onTimeout();
  }, ms);
  try {
    await open(live);
  } finally {
    clearTimeout(timer);
    endOpening(token);
  }
}
