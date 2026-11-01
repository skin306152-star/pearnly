// 「实操」工作区在这台设备上记的东西（只是方便，不是必须可靠的状态；任务本身——公司、月份、名字、置顶、状态——在站点的任务记录里，
// 跨设备，见 tasks.ts）：
//   - 准备中任务的准备（按任务号）：你说的那句话（主栏顶上的气泡，开始后照样留着）、开始前确认的回答、跳过的单、执行的电脑；
//   - 右侧栏开着没有（宽屏）、窄屏抽屉开着没有；
//   - 右栏各分区的手动开合（同步到服务端偏好的 operator 份里，prefs.ts）。
// 存在 localStorage（无痕窗口等存不了时只在本次有效）。

import { create } from "zustand";
import { prefChanged } from "@/lib/prefs-sync";
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

/** 分区开合最多记这么多条（按任务 + 分区，旧的先丢）。 */
export const FOLDS_MAX = 200;

type Saved = { preps: Record<string, Prep>; panel: boolean; folds: Record<string, boolean> };

const KEY = "cc.operator";

const isPrep = (value: unknown): value is Prep =>
  !!value && typeof value === "object" && typeof (value as Prep).prompt === "string" && Array.isArray((value as Prep).skipped);

/** 分区开合（形状不对的丢掉；只留最近的 FOLDS_MAX 条）。 */
export function parseFolds(value: unknown): Record<string, boolean> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const entries = Object.entries(value as Record<string, unknown>).filter((entry): entry is [string, boolean] => typeof entry[1] === "boolean");
  return Object.fromEntries(entries.slice(-FOLDS_MAX));
}

function load(): Saved {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "{}") as Partial<Saved>;
    const preps = Object.fromEntries(Object.entries(value.preps ?? {}).filter(([, prep]) => isPrep(prep)));
    return { preps, panel: value.panel !== false, folds: parseFolds(value.folds) };
  } catch (caughtError) { reportCaught(caughtError, "operator-store");
    return { preps: {}, panel: true, folds: {} };
  }
}

type State = Saved & {
  /** 窄屏、手机：右侧栏抽屉开着。 */
  drawer: boolean;
};

export const useOperatorStore = create<State>(() => ({ ...load(), drawer: false }));

useOperatorStore.subscribe((s) => {
  try {
    localStorage.setItem(KEY, JSON.stringify({ preps: s.preps, panel: s.panel, folds: s.folds }));
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

/** 分区手动开合（任务 + 分区 → 开 / 合）；没点过的按「处理完自动收起」。同步到服务端偏好（operator 份的 cards/folds）。 */
export function setFold(key: string, open: boolean) {
  useOperatorStore.setState((s) => {
    const folds = { ...s.folds };
    delete folds[key];
    folds[key] = open;
    return { folds: parseFolds(folds) };
  });
  prefChanged("operator", ["cards/folds"]);
}

export const setPanel = (panel: boolean) => useOperatorStore.setState({ panel });
export const setDrawer = (drawer: boolean) => useOperatorStore.setState({ drawer });
