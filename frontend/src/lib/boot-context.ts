// 把启动数据给所有页面用。
//
// 用 provide/inject 而不是给 RouterView 传 props：RouterView 只会把**路由参数**
// 当 props 传下去，自定义 prop 传不进去（写了也不报错，值是 undefined —— 那种错最难找）。

import type { InjectionKey } from "vue";
import type { ShellBoot } from "./shell";

export const SHELL_BOOT: InjectionKey<ShellBoot> = Symbol("pearnly-shell-boot");
