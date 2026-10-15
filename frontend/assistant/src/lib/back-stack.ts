// 手机返回键（安卓的返回键 / 返回手势、手机浏览器的「后退」）先关最上面那一层弹层，不直接退出 CC（2026-10-15 三星 A14 实测：
// 公司下拉、侧栏抽屉、设置弹窗、文件预览开着时按返回键，CC 立刻退到桌面）。
//
// 做法：开着几层，浏览器历史里就比进页时多几条本页记的条目（同一个地址，state 里记 ccBack = 第几层）。
//   · 多开一层 → pushState 一条；返回键 → popstate，历史少了一条而弹层没少 → 关最上面那一层；
//   · 弹层用别的方式关掉（点 ×、点外面、Esc、选了菜单项）→ 历史比弹层多 → history.go 退回去，不留多余的一条
//     （否则下次按返回键什么都不关，或者多退一步把页面退出去）；
//   · 每次变化都下一拍再对账：选菜单项打开弹窗（菜单关、弹窗开在同一拍）时层数没变，就不退也不加。
// 弹层有两种：组件库的弹窗与下拉菜单（Radix，看页面上开着的那几个，关最上面的那个等同按一次 Esc，组件库本来就只关最上层）；
// 本页自己画的（手机抽屉、手机上盖满屏的结果面板）用 useBackLayer 登记，排在组件库弹层下面。
//
// 只在触屏设备的浏览器里（含装成应用的网页）；电脑浏览器、CC 桌面版窗口里不碰浏览器历史，Esc 照旧。
// 本页的视图导航（lib/nav.ts 的后退 / 前进与深链接）只在内存里、不进浏览器历史，与这里互不相干；深链接的 replaceState
// 保留 history.state，不会抹掉这里记的层数。

import { useEffect, useRef } from "react";
import { isDesktopApp } from "@/lib/desktop";
import { OPEN_LAYERS } from "@/lib/layers";
import { touchOnly } from "@/lib/touch";

const KEY = "ccBack";
// 返回键要关的那一层没关上（弹层自己拒绝了）：等这么久后照实对账，把那一条补回去
const REFUSED_MS = 400;
// 本页自己 history.go 退回去，同一页面内的跳转很快就回 popstate；这么久还没回就当没退成
const STEP_BACK_MS = 1500;
// 页面上开着的组件库弹层（lib/layers.ts；侧栏键盘也用，不必拉进本文件的依赖）
export { OPEN_LAYERS } from "@/lib/layers";

export type BackEnv = {
  /** 当前历史条目上本页记的层数（不是本页记的：0）。 */
  depth(): number;
  push(depth: number): void;
  go(delta: number): void;
  /** 页面上开着几个组件库弹层。 */
  domLayers(): number;
  /** 关最上面那个组件库弹层（等同按一次 Esc）。 */
  closeDomTop(): void;
  later(run: () => void, ms: number): void;
};

export class BackStack {
  private own: Array<() => void> = [];
  private base: number;
  private traversing = false; // 本页自己发起的 history.go 还没回来
  private closingFrom: number | null = null; // 返回键关的那一层还没关上：层数还没降到它以下
  private queued = false;
  private steps = 0;

  constructor(private env: BackEnv) {
    // 开着弹层时刷新了页面：当前条目上已经记着层数，从它往上算（往回退会重新载入页面，不去动它）
    this.base = env.depth();
  }

  /** 登记本页自己画的一层，返回撤销。close：返回键要关它时调。 */
  register(close: () => void): () => void {
    const entry = () => close();
    this.own.push(entry);
    this.sync();
    return () => {
      const i = this.own.indexOf(entry);
      if (i >= 0) this.own.splice(i, 1);
      this.sync();
    };
  }

  count(): number {
    return this.own.length + this.env.domLayers();
  }

  /** 层数可能变了：下一拍再对账。 */
  sync(): void {
    if (this.queued) return;
    this.queued = true;
    this.env.later(() => {
      this.queued = false;
      this.reconcile();
    }, 0);
  }

  reconcile(): void {
    if (this.traversing) return;
    const n = this.count();
    if (this.closingFrom !== null) {
      if (n >= this.closingFrom) return; // 还在关
      this.closingFrom = null;
    }
    const want = this.base + n;
    let depth = this.env.depth();
    if (want > depth) {
      while (depth < want) this.env.push(++depth);
    } else if (want < depth) {
      const token = ++this.steps;
      this.traversing = true;
      this.env.go(want - depth);
      // 退不回去（浏览器没回 popstate）时不能一直卡着、也不反复退：从当前条目重新算起
      this.env.later(() => {
        if (!this.traversing || this.steps !== token) return;
        this.traversing = false;
        this.base = Math.max(0, this.env.depth() - this.count());
        this.reconcile();
      }, STEP_BACK_MS);
    }
  }

  onPopState(): void {
    if (this.traversing) {
      // 自己退的那几步回来了：其间又开了弹层的，照实补上
      this.traversing = false;
      this.reconcile();
      return;
    }
    const depth = this.env.depth();
    if (depth < this.base) this.base = depth;
    const n = this.count();
    if (this.base + n <= depth) {
      this.reconcile();
      return;
    }
    // 返回键：关最上面那一层
    const from = n;
    this.closingFrom = from;
    if (this.env.domLayers() > 0) this.env.closeDomTop();
    else this.own.at(-1)?.();
    this.env.later(() => {
      if (this.closingFrom !== from) return;
      this.closingFrom = null;
      this.reconcile();
    }, REFUSED_MS);
  }
}

function browserEnv(): BackEnv {
  const state = (): Record<string, unknown> | null => {
    const s: unknown = history.state;
    return s && typeof s === "object" ? (s as Record<string, unknown>) : null;
  };
  return {
    depth: () => {
      const value = state()?.[KEY];
      return typeof value === "number" && value > 0 ? value : 0;
    },
    push: (depth) => history.pushState({ ...(state() ?? {}), [KEY]: depth }, ""),
    go: (delta) => history.go(delta),
    domLayers: () => document.querySelectorAll(OPEN_LAYERS).length,
    closeDomTop: () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true, cancelable: true }));
    },
    later: (run, ms) => void window.setTimeout(run, ms),
  };
}

let stack: BackStack | null = null;

/** 页面启动时装一次（main.tsx）：只在触屏设备的浏览器里。 */
export function installBackKey(env: BackEnv = browserEnv(), enabled = touchOnly() && !isDesktopApp()): () => void {
  if (stack || !enabled) return () => undefined;
  const current = new BackStack(env);
  stack = current;
  const onPop = () => current.onPopState();
  window.addEventListener("popstate", onPop);
  // 组件库弹层都挂在 body 下（Portal）：body 的直接子节点增减 + 各处 data-state 变化就够，不看对话里的逐字更新
  const sync = () => current.sync();
  const mounts = new MutationObserver(sync);
  const states = new MutationObserver(sync);
  if (typeof document !== "undefined" && document.body) {
    mounts.observe(document.body, { childList: true });
    states.observe(document.body, { subtree: true, attributes: true, attributeFilter: ["data-state"] });
  }
  current.sync();
  return () => {
    window.removeEventListener("popstate", onPop);
    mounts.disconnect();
    states.disconnect();
    if (stack === current) stack = null;
  };
}

/** 本页自己画的一层（不是组件库弹窗 / 菜单）：open 时登记，返回键调 close 关它。 */
export function useBackLayer(open: boolean, close: () => void): void {
  const latest = useRef(close);
  latest.current = close;
  useEffect(() => {
    if (!open || !stack) return;
    return stack.register(() => latest.current());
  }, [open]);
}
