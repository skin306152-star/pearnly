// 对话里的上传状态卡片（评估 docs/work/2026-11-04-cc-code-style.md 第 5 节；直写、代录同一个）：看不到状态是重复上传、重复扣费的根因，
// 卡片一直留在上传那一刻的位置，下面一行写此刻的状态——上传中 N%、排队中、识别中 n/m 页、已识别 N 张（· N 张不在 10 月）、失败。
// 同一份再传就是又一张卡片、照常识别，不加「已传过」之类的提示（产品口径：上传识别都扣费）。
// 样子不新造：卡片是你发的消息里的附件卡片（attachment.tsx 的 AttachmentCard），那一行字同步骤行（在动的小点同 steps.tsx 进行中的那一步，
// 失败同步骤行尾的危险色「失败」），上传失败的「重试」是卡片按钮（.cc-card-btn）。识别失败照产品口径不另做重试。

import type { FC } from "react";
import { AttachmentCard, attachmentTypeLine } from "@/components/assistant-ui/attachment";
import { TypingDots } from "@/components/typing-dots";
import { Marquee } from "@/components/ui/marquee";
import { steps, t } from "@/i18n";
import type { UploadCardProps } from "./types";
import { uploadBusy, uploadText } from "./upload-state";

export const UploadCard: FC<UploadCardProps> = ({ name, size, pages, image, state, onRetry, onOpen }) => {
  const typeLine = attachmentTypeLine(name, size, pages);
  const label = t("attachment").replace("{0}", [name, typeLine].filter(Boolean).join(" · "));
  const busy = uploadBusy(state);
  const failed = state.kind === "failed";
  const text = uploadText(state);
  return (
    <div data-slot="cc-upload-card" data-state={state.kind} className="flex w-(--cc-attach-w) flex-col gap-(--cc-space-4)">
      <AttachmentCard name={name} label={label} typeLine={typeLine} {...(image ? { image } : {})} {...(onOpen ? { onOpen } : {})} />
      <div className="flex min-w-0 items-center gap-(--cc-space-4) text-ink-3 text-caption" aria-live="polite">
        {busy && <TypingDots size="sm" className="shrink-0" label={text} />}
        {failed && <span className="cc-step-failed">{steps.failed}</span>}
        {text && <Marquee className="min-w-0 truncate">{text}</Marquee>}
      </div>
      {failed && state.stage === "upload" && onRetry && (
        <div className="cc-card-actions">
          <button type="button" className="cc-card-btn" onClick={onRetry}>
            {t("retry")}
          </button>
        </div>
      )}
    </div>
  );
};
