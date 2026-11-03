// 「待处理」里代录一组的数据（attention.ts 取、重取）：单独一个模块，待处理页只引它，不把代录的数据源、桥带进待处理页。

import { create } from "zustand";
import type { TaskRecord } from "./types";

type Attention = { loaded: boolean; total: number; companies: Record<string, number>; tasks: TaskRecord[] };
export const useAttention = create<Attention>(() => ({ loaded: false, total: 0, companies: {}, tasks: [] }));
