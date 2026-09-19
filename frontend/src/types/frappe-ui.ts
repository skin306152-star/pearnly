// 官方组件库（Frappe UI）在**类型检查**这一侧的边界。
//
// 为什么需要这个文件：frappe-ui 只发**源码**（`exports.import` 指向 `./src/index.ts`），
// 不发 d.ts。于是 `vue-tsc` 会顺着 import 进去检查它内部每个文件，而那些文件在我们这套
// 设置下有一批报错（verbatimModuleSyntax 要求的 type-only import、未使用形参、
// `this` 隐式 any……）。那不是我们的代码，替它检查没有意义，也不该因此挡住构建。
//
// 做法：在 tsconfig 的 `paths` 里把 `frappe-ui` 指到本文件。于是
//   - **类型检查**用这份声明（我们自己的代码仍然严格检查）；
//   - **构建**不受影响 —— Vite 用自己的解析规则，拿到的还是真正的官方组件。
//
// 用到的组件在这里逐个列出（而不是 `export *`），这样"用了官方哪些组件"一眼可见。
// 用了新组件就往这里加一行 —— 加了才知道边界在哪。
// 注意全部用 `declare`：本文件只声明类型，**没有实现** ——
// 实现是官方包真正的那份（构建时由 Vite 解析过去）。
import type { Component } from "vue";

declare const FileUploader: Component;
declare const Alert: Component;
declare const ListView: Component;
declare const Sidebar: Component;
declare const SidebarItem: Component;
declare const SidebarHeader: Component;
declare const SidebarSection: Component;

declare const Button: Component;
declare const Input: Component;
declare const TextInput: Component;
declare const Select: Component;
declare const Checkbox: Component;
declare const Switch: Component;
declare const Dialog: Component;
declare const Tooltip: Component;
declare const Popover: Component;
declare const Dropdown: Component;
declare const Badge: Component;
declare const Avatar: Component;
declare const Spinner: Component;
declare const LoadingIndicator: Component;
declare const ErrorMessage: Component;
declare const FeatherIcon: Component;
declare const Card: Component;
declare const Divider: Component;
declare const Tabs: Component;
declare const TabButtons: Component;
declare const FormControl: Component;
declare const FormLabel: Component;
declare const Breadcrumbs: Component;
declare const Toast: Component;
declare const ConfirmDialog: Component;

declare function toast(options: Record<string, unknown>): unknown;
declare function call(method: string, params?: Record<string, unknown>): Promise<unknown>;
declare function createResource(options: Record<string, unknown>): unknown;
declare function frappeRequest(options: Record<string, unknown>): Promise<unknown>;

export {
  FileUploader,
  Alert,
  ListView,
  Sidebar,
  SidebarItem,
  SidebarHeader,
  SidebarSection,
  Button,
  Input,
  TextInput,
  Select,
  Checkbox,
  Switch,
  Dialog,
  Tooltip,
  Popover,
  Dropdown,
  Badge,
  Avatar,
  Spinner,
  LoadingIndicator,
  ErrorMessage,
  FeatherIcon,
  Card,
  Divider,
  Tabs,
  TabButtons,
  FormControl,
  FormLabel,
  Breadcrumbs,
  Toast,
  ConfirmDialog,
  toast,
  call,
  createResource,
  frappeRequest,
};
