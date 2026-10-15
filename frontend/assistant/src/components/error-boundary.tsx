// 整页最外层的错误边界（main.tsx）：渲染或副作用里抛错时，React 19 没有错误边界就卸掉整棵树、整页变白
// （装成应用后没有地址栏，只能杀掉重开）。这里接住，换成一个「重新加载」按钮；错误照常写进控制台。
// 给了 fallback 的（例如结果面板的 Word 预览）只换掉出错的那一块，显示 fallback。

import { RotateCwIcon } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { t } from "@/i18n";

type Props = { children: ReactNode; fallback?: ReactNode };
type State = { failed: boolean };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("[cc] page error", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    if (this.props.fallback !== undefined) return this.props.fallback;
    return (
      <div role="alert" className="flex h-(--cc-vh) w-full items-center justify-center bg-app">
        <button type="button" className="cc-btn" onClick={() => location.reload()}>
          <RotateCwIcon className="size-4" />
          {t("reload")}
        </button>
      </div>
    );
  }
}
