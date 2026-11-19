// 手机上等回答的允许 / 拒绝卡（照 Claude 手机端的问题卡片，用户 10-17）：浮在输入框上方，大圆角、弹层底；问话大号字、
// 右上圆形「×」＝拒绝，改动照常列（多了「还有 N 项」），右下大号胶囊「允许」。对话流里那一步只列问话与改动、不再画按钮
// （steps.tsx 的 AskStep）。多张待答一次只浮最早的一张，答完浮下一张；答完浮层消失，对话流里是点完的卡片。
// 放在对话底部输入框那一块里（thread.tsx 的 ViewportFooter）：键盘弹出、抽屉打开、页面滚动时跟着输入框走。
// 不是弹窗：Esc / 返回键不当拒绝，只有「×」。电脑不浮（卡片在对话流里）。
// 通用提问卡片 ask_user（B24，主会话 11-09 定，照 Claude）：电脑、手机都浮在这里，宽同输入框，卡片照旧是 ask-card 那一张；
// 对话流里那一步只留「正在提问」（ask-card.tsx 的 AskUserCard）。答完浮层消失，对话流里是点完的卡片。

import { XIcon } from "lucide-react";
import { useIsMobile } from "@/hooks/use-mobile";
import { firstPendingApproval, floatingAsk } from "@/lib/approval-card";
import type { ToolPart } from "@/lib/managed-agents/reducer";
import { useActiveSessionSnapshot } from "@/lib/managed-agents/runtime-provider";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { useToolLabels } from "@/lib/tool-labels";
import { t } from "@/i18n";
import { ApprovalFailure, ChangeList } from "./approval-bar";
import { AskUserBody } from "./ask-card";
import { ASK_TOOL } from "./ask-shared";
import { GUIDE_TOOL } from "./guide-shared";
import { askText } from "./steps";

export function ApprovalSheet() {
  const phone = useIsMobile();
  const snapshot = useActiveSessionSnapshot();
  const labels = useToolLabels();
  if (!snapshot.sessionId) return null;
  const asking = floatingAsk(snapshot.messages);
  if (asking?.type === "tool-call") {
    return (
      <section key={asking.approval?.id} data-slot="cc-ask-float" className="cc-ask-float">
        <AskUserBody part={asking} toolResult={asking.result} sessionId={snapshot.sessionId} status={false} floating />
      </section>
    );
  }
  if (!phone) return null;
  const part = firstPendingApproval<ToolPart | { type: "text" | "reasoning"; approval?: undefined }>(snapshot.messages);
  if (!part || part.type !== "tool-call" || !part.approval || part.toolName === ASK_TOOL || part.toolName === GUIDE_TOOL) return null;
  const { approval } = part;
  const sessionId = snapshot.sessionId;
  const respond = (approved: boolean) => getSessionController(sessionId).respondToApproval(approval.id, approved);
  const question = askText(part.toolName, part.args, approval.ask, labels);
  return (
    <section key={approval.id} data-slot="cc-ask-sheet" aria-label={question} className="cc-ask-sheet">
      <div className="flex items-start gap-(--cc-space-12)">
        <p className="cc-ask-sheet-q">{question}</p>
        <button type="button" className="cc-card-btn" data-icon="" aria-label={t("deny")} onClick={() => respond(false)}>
          <XIcon />
        </button>
      </div>
      {/* 事实行（这一步会动到哪家公司、哪本账套；服务端给了才出），同电脑上的允许卡 */}
      {approval.note ? <p className="cc-ask-note">{approval.note}</p> : null}
      <ChangeList preview={approval.preview} />
      <div className="cc-ask-foot">
        <ApprovalFailure message={approval.failure} />
        <div className="cc-card-actions">
          <button type="button" className="cc-card-btn" data-primary="" onClick={() => respond(true)}>
            {t("allow")}
          </button>
        </div>
      </div>
    </section>
  );
}
