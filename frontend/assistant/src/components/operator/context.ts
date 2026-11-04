// 代录工作区的上下文：外壳别处也要用，单放一处，不把数据源（live-source）一起带进去。
// 状态与动作本身在 controller.tsx（OperatorProvider 挂上这里的上下文）。

import { createContext, useContext } from "react";
import type { Operator } from "./controller";

export const OperatorContext = createContext<Operator | null>(null);

/** 不在实操里（没有实操的产品、外壳别处）回 null。 */
export const useOperatorOrNull = (): Operator | null => useContext(OperatorContext);
