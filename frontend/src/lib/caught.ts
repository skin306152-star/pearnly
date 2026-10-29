// 统一出错回传（吞错闸门，主会话 10-28）：代码里接住的错误一律交给站点页面挂的 `public/desk/errors.js`（window.pearnlyCaught）。
// 请求被站点拒收 / 网络失败那里自己会跳过（服务端已记、页面已提示）；其余前端异常记进 Error Log。
// `scripts/check-swallowed-errors.py` 要求每个 catch 重抛，或调这里的 `reportCaught` / `withReport`。
type Report = (error: unknown, kind: string) => void;

export function reportCaught(error: unknown, kind: string): void {
  (window as Window & { pearnlyCaught?: Report }).pearnlyCaught?.(error, kind);
}

/** `.catch(withReport(原来的处理, "哪里"))`：先回传，再照原来的处理。 */
export function withReport<T>(fallback: ((error: unknown) => T) | T, kind: string): (error: unknown) => T {
  return (error: unknown) => {
    reportCaught(error, kind);
    return typeof fallback === "function" ? (fallback as (error: unknown) => T)(error) : fallback;
  };
}
