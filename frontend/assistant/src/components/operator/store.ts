// 「实操」工作区在这台设备上记的东西（只是方便，不是必须可靠的状态；任务本身——公司、月份、名字、置顶、状态——在站点的任务记录里，
// 跨设备，见 tasks.ts）：准备中任务的准备（按任务号）——新建时带的那句话（会话最上面的气泡）、开始前确认的回答、跳过的单、
// 执行的电脑。存在 localStorage（无痕窗口等存不了时只在本次有效）。旧版本存过的右栏开关、分区开合认不出就不读。

import { create } from "zustand";
import { reportCaught } from "@/lib/caught";

/** 一个准备中任务在这台设备上的准备。 */
export type Prep = {
  prompt: string;
  /** 执行的电脑（"" = 本人自己的电脑；手机上新建时选）。 */
  computer: string;
  /** 开始前确认的回答：{问题 key: 选的 code}。 */
  answers: Record<string, string>;
  /** 开始前确认里点了「跳过」的单。 */
  skipped: string[];
};

/** 准备中的任务（站点的任务记录 + 这台设备上的准备），controller.tsx 拼。 */
export type Draft = Prep & { id: string; company: string; company_title: string; period: string; created: number };

export const EMPTY_PREP: Prep = { prompt: "", computer: "", answers: {}, skipped: [] };

type Saved = { preps: Record<string, Prep> };

const KEY = "cc.operator";

const isPrep = (value: unknown): value is Prep =>
  !!value && typeof value === "object" && typeof (value as Prep).prompt === "string" && Array.isArray((value as Prep).skipped);

function load(): Saved {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "{}") as Partial<Saved>;
    const preps = Object.fromEntries(Object.entries(value.preps ?? {}).filter(([, prep]) => isPrep(prep)));
    return { preps };
  } catch (caughtError) { reportCaught(caughtError, "operator-store");
    return { preps: {} };
  }
}

export const useOperatorStore = create<Saved>(() => load());

useOperatorStore.subscribe((s) => {
  try {
    localStorage.setItem(KEY, JSON.stringify({ preps: s.preps }));
  } catch (caughtError) { reportCaught(caughtError, "operator-store");
    // 存不了：这次照样能用
  }
});

export const prepOf = (id: string): Prep => useOperatorStore.getState().preps[id] ?? EMPTY_PREP;

export function setPrep(id: string, patch: Partial<Prep>) {
  if (!id) return;
  useOperatorStore.setState((s) => ({ preps: { ...s.preps, [id]: { ...(s.preps[id] ?? EMPTY_PREP), ...patch } } }));
}

export function forgetPrep(id: string) {
  useOperatorStore.setState((s) => {
    if (!(id in s.preps)) return s;
    const preps = { ...s.preps };
    delete preps[id];
    return { preps };
  });
}
