
// The human-in-the-loop gate for an always_ask tool.
//
// Managed Agents parked the turn on this tool call and is waiting for a
// user.tool_confirmation. Allow lets it run; Deny sends the agent a message
// it sees as the tool's result ("Denied: not on the production box"), so it
// can adjust instead of retrying blindly. Rendered by any tool card whose
// part carries an unresolved `approval` (see reducer.ts).

import { CheckIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { t } from "@/i18n";

type Approval = {
  id: string;
  approved?: boolean;
  reason?: string;
  preview?: Array<{ field: string; before: string; after: string }>;
};

export function ApprovalBar({
  approval,
  respondToApproval,
  className,
}: {
  approval?: Approval;
  respondToApproval?: (response: { approved: boolean; reason?: string }) => void;
  what?: string;
  className?: string;
}) {
  // Already answered (in this tab, or replayed from history): show the verdict.
  if (approval && approval.approved !== undefined) {
    return (
      <div
        className={cn(
          "flex items-center gap-1.5 text-xs",
          approval.approved ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400",
          className,
        )}
      >
        {approval.approved ? <CheckIcon className="size-3.5" /> : <XIcon className="size-3.5" />}
        <span className="font-medium">{approval.approved ? t("allowed") : t("denied")}</span>
      </div>
    );
  }

  // No pending gate on this call.
  if (!approval || !respondToApproval) return null;

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {approval.preview && approval.preview.length > 0 && (
        <ul className="rounded-md border border-[#e5e5e5] bg-[#fafafa] px-3 py-2 text-[13px] text-[#3d3d3a] leading-6">
          {approval.preview.map((change, i) => (
            <li key={i} className="break-words">
              <span className="me-2 text-[#8a887f]">{change.field}</span>
              {change.before ? (
                <>
                  <span className="line-through decoration-[#b5b3aa]">{change.before}</span>
                  <span className="px-1.5 text-[#8a887f]">→</span>
                </>
              ) : null}
              <span className="font-medium">{change.after}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-2">
      <Button size="sm" onClick={() => respondToApproval({ approved: true })}>
        <CheckIcon className="size-3.5" />
        {t("allow")}
      </Button>
      <Button size="sm" variant="outline" onClick={() => respondToApproval({ approved: false })}>
        <XIcon className="size-3.5" />
        {t("deny")}
      </Button>
      </div>
    </div>
  );
}
