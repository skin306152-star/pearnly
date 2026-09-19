// 站点启动数据：这个壳是谁、买了什么、csrf 是什么。
//
// 为什么要有这个接口：前端**不能自己决定**菜单显示什么 —— ADR-005 要求
// "前端根据后端返回的授权展示菜单"。而"这家租户买了哪些功能"这个事实
// 只在后端，前端必须问。

import { call, setCsrfToken } from "./api";

export interface ShellBoot {
  /** 本站点的产品：cowork / erp / dms */
  product: string;
  product_label: string;
  /** **买到的**功能码（不是产品声明的全部功能） */
  features: string[];
  user: string;
  full_name: string;
  language: string;
  csrf_token: string;
  native_account?: boolean;
}

let cached: ShellBoot | null = null;

export async function loadShell(): Promise<ShellBoot> {
  if (cached) return cached;
  const boot = await call<ShellBoot>("pearnly_core.shell.describe");
  setCsrfToken(boot.csrf_token);
  cached = boot;
  return boot;
}

/** 只有端到端验证脚本才会用到：清掉缓存好重新问一次。 */
export function resetShellCache(): void {
  cached = null;
}

export function hasFeature(boot: ShellBoot, code: string): boolean {
  return boot.features.includes(code);
}
