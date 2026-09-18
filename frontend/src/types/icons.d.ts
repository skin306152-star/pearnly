// unplugin-icons 的虚拟模块：`~icons/lucide/home` 这类导入在构建时被换成图标组件。
// 这些路径在磁盘上不存在，所以要有声明，否则类型检查过不去。
declare module "~icons/*" {
  import type { Component } from "vue";
  const component: Component;
  export default component;
}
