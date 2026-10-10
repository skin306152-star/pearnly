// 工具在对话步骤里显示的名字（站点按当前用户语言给出，来自各工具声明的 label）。

import { useEffect, useState } from "react";
import { get } from "@/lib/frappe";

let cached: Promise<Record<string, string>> | null = null;

export function useToolLabels(): Record<string, string> {
  const [labels, setLabels] = useState<Record<string, string>>({});
  useEffect(() => {
    cached ??= get<Record<string, string>>("tool_labels").catch(() => ({}));
    void cached.then(setLabels);
  }, []);
  return labels;
}
