// 「实操」侧栏的个人排列（同对话侧，按人存在服务端、跨设备同步，lib/prefs-sync.ts）：存在服务端偏好的 operator 份里，
// 与对话的 order / view 分开——任务号不进对话的手排顺序：
//   order/… — 侧栏手排的公司组、置顶与组里任务的顺序（sidebar-order.ts 的另一份实例，本机缓存键 cc.operator.sidebar.order:<用户>）
// 侧栏各组的折叠仍按设备记（sidebar-fold.ts 的另一份实例，键 cc.operator.sidebar.collapsed:<用户>），同对话。
// 旧版本在这一份里存过右栏分区开合（cards/folds）：右栏已删，服务端偏好里留着的认不出就不读，不迁移。

import { createSidebarFold } from "@/lib/managed-agents/sidebar-fold";
import { createSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { bindSeenUser, useTasks } from "./tasks";

export const OPERATOR_PREF = "operator";

export const taskOrder = createSidebarOrder({
  key: (user) => `cc.operator.sidebar.order:${user}`,
  pref: OPERATOR_PREF,
  prefix: "order/",
  seenGroups: () => useTasks.getState().seenAt,
});

export const taskFold = createSidebarFold("cc.operator.sidebar.collapsed");

/** 知道是谁了：手排顺序、组的折叠、看过没有换成这个人的。 */
export function bindOperatorUser(user: string) {
  if (!user) return;
  taskOrder.bindOrderUser(user);
  taskFold.bindFoldUser(user);
  bindSeenUser(user);
}
