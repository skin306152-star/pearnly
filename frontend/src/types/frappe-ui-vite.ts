// `frappe-ui/vite` 的类型桩。理由见同目录 frappe-ui.ts：它是 .js，没有类型，
// 而类型检查不该被它的内部实现挡住。
import type { Plugin } from "vite";

export interface FrappeUiViteOptions {
  lucideIcons?: boolean | object;
  frappeProxy?: boolean | object;
  jinjaBootData?: boolean | object;
  buildConfig?: boolean | object;
  frontendRoute?: string;
}

declare function frappeui(options?: FrappeUiViteOptions): Plugin[];

export default frappeui;
