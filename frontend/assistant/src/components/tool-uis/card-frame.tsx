// 对话里的卡片骨架（一套，所有卡共用；用户 11-16「同一套，不许两套」）：照线上已在用的允许卡 / 代录卡（steps.tsx 的 AskStep、
// operator-card.tsx 的 OperatorCard）——
//   头：.cc-ask-q 灰底一行——允许式（删除 / 结束 / 立即运行这类）写问句（服务端 consent_title），其余写业务名或代录的「写入 公司 · 账套 · 电脑」；
//   问句下面一行浅色事实（.cc-ask-note，服务端给，没有不出）；
//   体：内容；
//   脚：.cc-ask-foot 里出错一行（Alert）+ 靠右一组卡片按钮（Button size="xs"：次按钮 variant="secondary" 在左、主按钮默认样式在右）。
// 按钮文字的规则：次按钮 = 取消 / 跳过 / 拒绝，主按钮 = 下一步 / 提交 / 写入 / 允许；一张卡最多一个主按钮。
// 提问（问卷版式，ask-card.tsx 的 AskCard）是同一族：头是题目栏 + 翻页，脚是同样两档卡片按钮靠右。新卡只能用这两个，不自造。

import type { FC, ReactNode } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export type FrameButton = { slot: string; label: string; primary?: boolean; disabled?: boolean; onClick: () => void };

/** 卡片脚：出错一行 + 靠右按钮（次在左、主在右；按给的顺序画，主按钮放最后）。 */
export const CardFoot: FC<{ buttons: readonly FrameButton[]; failure?: string; busy?: boolean }> = ({ buttons, failure, busy }) => {
  if (!buttons.length && !failure) return null;
  const ordered = [...buttons.filter((b) => !b.primary), ...buttons.filter((b) => b.primary)];
  return (
    <div className="cc-ask-foot" data-slot="cc-card-foot">
      {failure ? (
        <Alert variant="destructive" data-slot="cc-ask-failure">
          <AlertDescription className="line-clamp-(--cc-alert-lines)">{failure}</AlertDescription>
        </Alert>
      ) : null}
      {ordered.length > 0 && (
        <div className="cc-card-actions">
          {ordered.map((button) => (
            <Button key={button.slot} type="button" size="xs" variant={button.primary ? "default" : "secondary"} data-slot={button.slot}
              disabled={busy || button.disabled} onClick={button.onClick}>
              {button.label}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
};

/** 卡片：头 + 体 + 脚。slot / attrs 给流程脚本认。 */
export const CardFrame: FC<{
  title: ReactNode;
  /** 标题栏下面一行浅色事实（同允许卡的 note），没有不出 */
  note?: string;
  children?: ReactNode;
  buttons?: readonly FrameButton[];
  failure?: string;
  busy?: boolean;
  slot?: string;
  attrs?: Record<string, string | undefined>;
  phase?: string;
}> = ({ title, note, children, buttons = [], failure, busy, slot, attrs, phase = "ask" }) => (
  <div className="cc-steps-card" data-slot={slot} {...attrs}>
    <div className="aui-step cc-ask" data-phase={phase}>
      <div className="cc-ask-q" data-slot="cc-card-head">{title}</div>
      {note ? <p className="cc-ask-note" data-slot="cc-card-note">{note}</p> : null}
      {children}
      <CardFoot buttons={buttons} failure={failure} busy={busy} />
    </div>
  </div>
);
