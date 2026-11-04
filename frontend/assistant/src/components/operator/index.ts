// 代录（CC 在 Express 窗口里逐张填写，Cowork 与 ERP 都有）。方案 cc-unified-plan 14：代录任务就是一段标准对话会话——同一个 thread、
// runtime、新建页、侧栏、活动状态与停止键；代录的状态由服务端的代录事件源翻成统一对话事件，卡片与步骤在 components/tool-uis/operator-*。
// 这里只留：左上角「直写 / 实操」切换（切换只改侧栏的模式筛选与新建时带的 mode）、执行电脑上的桥（进度合成 pearnly.progress、取控制、收尾）。

export { ModeSwitch, MODES, type OperatorMode } from "./mode-switch";
export { useOperatorBridge } from "./bridge";
