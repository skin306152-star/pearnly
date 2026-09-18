import { inject } from "vue";
import { SHELL_BOOT } from "../lib/boot-context";
import type { ShellBoot } from "../lib/shell";

export function useBoot(): ShellBoot {
  const boot = inject(SHELL_BOOT);
  if (!boot) {
    throw new Error("页面必须挂在产品壳里（启动数据没提供）");
  }
  return boot;
}
