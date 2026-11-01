// 侧栏各组的折叠（设计文档 3.4；thread-list.tsx 的公司组 / 已置顶 / 最近 / 各月，ERP、DMS 的按日分组）。
//
// 每台设备、每个用户各记各的（localStorage，键带用户，同「分组和排序」sidebar-view.ts 的做法），电脑、手机一样：
//   - 页面打开时还不知道是谁（资料要等 shell.describe）：先按这台设备上次登录的人（cc.sidebar.user）读，首屏就是他记下的；
//   - 知道是谁了（bindFoldUser）：换成这个人的；这个人在这台设备上还没记过时沿用设备上原来的那份（改版前的 cc.sidebar.collapsed，
//     或资料到之前点的），不会一登录就全变回默认；
//   - 一份全局的 store：电脑窗口拉窄换成手机抽屉、抽屉重开都读同一份，不各自从本机再读一遍。
// 测试窗口第 3 轮（手机）：重开 App 后各组都收着，电脑却记得。默认「前 5 家展开」现在只数有对话的公司（sidebar-groups.ts），
// 没有对话的空公司组不再占掉这 5 个位置。存不了（无痕窗口）时本次照样生效。

import { create } from "zustand";
import { toggleGroup } from "./sidebar-groups";
import { reportCaught } from "../caught";

export const DEVICE_FOLD_KEY = "cc.sidebar.collapsed";
export const foldKey = (user: string) => `${DEVICE_FOLD_KEY}:${user}`;
const USER_KEY = "cc.sidebar.user"; // sidebar-view.ts 登录后写的「这台设备上次是谁」

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function get(key: string): string | null {
  try {
    return storage()?.getItem(key) ?? null;
  } catch (caughtError) { reportCaught(caughtError, "sidebar-fold");
    return null;
  }
}

function parse(raw: string | null | undefined): Set<string> | null {
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? new Set(value.filter((key): key is string => typeof key === "string")) : null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- 一份实例
// 对话侧一份（键 cc.sidebar.collapsed:<用户>），实操侧另一份（components/operator/prefs.ts，键另起）：同一家公司的组键
// project:<编号> 两边不互相影响。
export function createSidebarFold(deviceKey: string) {
  const keyOf = (user: string) => `${deviceKey}:${user}`;
  /** 这个人记下的；没记过用设备上的那份；都没有是空的（各组按默认）。 */
  const readOf = (user: string) => (user ? parse(get(keyOf(user))) : null) ?? parse(get(deviceKey)) ?? new Set<string>();

  let owner = get(USER_KEY) ?? "";

  const useSidebarFold = create<{ collapsed: ReadonlySet<string> }>(() => ({ collapsed: readOf(owner) }));

  function save(next: ReadonlySet<string>) {
    useSidebarFold.setState({ collapsed: next });
    try {
      storage()?.setItem(owner ? keyOf(owner) : deviceKey, JSON.stringify([...next]));
    } catch (caughtError) { reportCaught(caughtError, "sidebar-fold");
      // 无痕窗口等存不了：本次照样折叠
    }
  }

  /** 知道当前用户了：换成这个人记下的（同一人不变）。资料到之前点过的、这个人还没记过的，照当前的存给他。 */
  function bindFoldUser(user: string) {
    if (!user || user === owner) return;
    const before = owner;
    owner = user;
    const mine = parse(get(keyOf(user)));
    if (mine) useSidebarFold.setState({ collapsed: mine });
    else if (!before) save(useSidebarFold.getState().collapsed);
    else useSidebarFold.setState({ collapsed: readOf(user) });
  }

  /** 按日分组、ERP / DMS：点组名开 / 收（记组键）。 */
  function toggleFold(key: string) {
    const next = new Set(useSidebarFold.getState().collapsed);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    save(next);
  }

  /** 公司组等：前 5 家默认展开，其余默认收起（键 project:<编号> / open:project:<编号>，见 sidebar-groups 的 groupOpen）。 */
  function toggleFoldSection(section: { key: string; rank?: number }) {
    save(toggleGroup(section, useSidebarFold.getState().collapsed));
  }

  return { useSidebarFold, bindFoldUser, toggleFold, toggleFoldSection };
}

export type SidebarFoldStore = ReturnType<typeof createSidebarFold>;

/** 对话侧的那一份（模块原来的各个出口照旧是它的）。 */
export const { useSidebarFold, bindFoldUser, toggleFold, toggleFoldSection } = createSidebarFold(DEVICE_FOLD_KEY);
