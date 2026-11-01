// 左上角切换（照 Claude 的分段：底框 + 选中格浅底细边）：左「直写」（对话，直接写入 Express 账套）、右「实操」（在 Express 窗口中
// 逐张填写）。只显示 18 的图标、点按区 30；悬停与键盘聚焦只出一行说明（用户 10-29：不显示「直写」「实操」两个名字），aria-label 是
// 标题。右边图标照 Claude 桌面版 Code 的「</>」（用户 10-29）。放在侧栏最上面那一行
// （☰、侧栏开关、←、→ 那行）的右端；侧栏收起时跟着那一行留在顶栏（由外壳放）。快捷键 Ctrl / ⌘ + Shift + 1 / 2 由外壳监听。
// 颜色 currentColor，尺寸与选中样子在 globals.css 的 .cc-op-seg*。

import type { FC } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { op } from "@/i18n";
import { cn } from "@/lib/utils";

export type OperatorMode = "chat" | "operator";

export const MODES: readonly OperatorMode[] = ["chat", "operator"];

/** 两个图标（24 网格、圆头、不填充；线宽 2 在 globals.css 的 --cc-op-seg-stroke）。圆角矩形写成路径（同一个形）。 */
const DirectIcon: FC = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M12.5 18.6c-.8.1-1.6.1-2.5 0L6 20l.8-3.1C5 15.6 4 13.9 4 12c0-4 3.6-7.2 8-7.2s8 3.2 8 7.2" />
    <circle cx="17.5" cy="17" r="3" />
  </svg>
);

const HandsOnIcon: FC = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="m18 16 4-4-4-4" />
    <path d="m6 8-4 4 4 4" />
    <path d="m14.5 4-5 16" />
  </svg>
);

/** 每一侧的标题、说明与图标（按界面语言）。 */
export const modeText = (mode: OperatorMode) =>
  mode === "chat" ? { title: op.direct, hint: op.directHint } : { title: op.handsOn, hint: op.handsOnHint };

export const ModeSwitch: FC<{ value: OperatorMode; onChange: (mode: OperatorMode) => void; className?: string }> = ({
  value,
  onChange,
  className,
}) => (
  <div role="radiogroup" aria-label={op.mode} data-slot="cc-mode-switch" className={cn("cc-op-seg", className)}>
    {MODES.map((mode) => {
      const { title, hint } = modeText(mode);  // 提示只说明，标题只给读屏
      return (
        <Tooltip key={mode}>
          <TooltipTrigger asChild>
            <button
              type="button"
              role="radio"
              aria-checked={value === mode}
              aria-label={title}
              data-mode={mode}
              className="cc-op-seg-btn"
              onClick={() => value !== mode && onChange(mode)}
            >
              {mode === "chat" ? <DirectIcon /> : <HandsOnIcon />}
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" data-slot="cc-mode-tip">
            {hint}
          </TooltipContent>
        </Tooltip>
      );
    })}
  </div>
);
