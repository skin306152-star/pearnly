// 左上角切换（直写 / 实操）与「实操」工作区（CC 在 Express 窗口里逐张填写；11-04 起任务的一切都在一条会话里，没有右侧栏）。
// 外壳（assistant.tsx）接：左上角切换、实操视图（顶栏标题、会话主栏）、侧栏换成任务列表；数据源是 live-source.ts（站点接口 +
// CC 桌面版桥）。mock-source.ts 只给预览与测试，不从这里导出。

export { ModeSwitch, MODES, type OperatorMode } from "./mode-switch";
export { createLiveSource, type LiveOperatorSource } from "./live-source";
export { OperatorProvider, useOperator } from "./controller";
export { OperatorSidebar } from "./operator-sidebar";
export { watchTasks } from "./tasks";
export { OperatorMain, OperatorTitle } from "./workspace";
export type * from "./types";
export { REASONS, BATCH_STOPPERS, levelOf } from "./types";
