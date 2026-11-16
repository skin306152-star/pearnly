// 引导卡的复核与结果（引导式分支 3 F3；服务端 review(ctx, draft) / result(ctx, draft)）。G1 先把服务端给的照原样一行一行画出来，
// G2 换成通用的复核卡（表格、点行回那一步、单据版就地改）与结果卡（每张一行、文件下载、追问）。
// 「写入」用输入框上方那一枚已上线的词（recordNow）；allow（N5）在 G2 画成「拒绝 / 允许」。

import type { FC } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item";
import { guide, t } from "@/i18n";
import { cancelGuide, confirmGuide, type GuideView } from "@/lib/guide-run";

type Act = (call: () => Promise<GuideView>) => void;

export const GuideReview: FC<{ view: GuideView; busy: boolean; failure: string; act: Act }> = ({ view, busy, failure, act }) => {
  const review = view.review!;
  return (
    <div data-slot="cc-guide-review" className="flex min-w-0 flex-col gap-(--cc-space-8)">
      <ItemGroup>
        {(review.lines ?? []).map((line) => (
          <Item key={line.key} size="sm" data-key={line.key}>
            <ItemContent><ItemDescription>{line.label}</ItemDescription></ItemContent>
            <ItemActions><span>{line.value}</span></ItemActions>
          </Item>
        ))}
      </ItemGroup>
      {(review.tables ?? []).map((table) => (
        <ItemGroup key={table.key} data-key={table.key}>
          <Item size="sm"><ItemContent><ItemTitle>{table.label}</ItemTitle></ItemContent></Item>
          {table.rows.map((row) => (
            <Item key={row.code} size="sm" data-code={row.code}>
              <ItemContent>
                <ItemTitle>{row.name || row.code}</ItemTitle>
                {row.note ? <ItemDescription>{row.note}</ItemDescription> : null}
              </ItemContent>
              <ItemActions><span className="tabular-nums">{row.amount}</span></ItemActions>
            </Item>
          ))}
        </ItemGroup>
      ))}
      {review.total ? (
        <Item size="sm" data-slot="cc-guide-total">
          <ItemContent><ItemTitle>{guide.total}</ItemTitle></ItemContent>
          <ItemActions><span className="tabular-nums">{review.total}</span></ItemActions>
        </Item>
      ) : null}
      {review.problem_text ? <Alert variant="destructive"><AlertDescription>{review.problem_text}</AlertDescription></Alert> : null}
      {failure ? <Alert variant="destructive"><AlertDescription className="line-clamp-(--cc-alert-lines)">{failure}</AlertDescription></Alert> : null}
      <div className="cc-card-actions">
        <Button variant="ghost" size="xs" disabled={busy} onClick={() => act(() => cancelGuide(view.run))}>{t("cancel")}</Button>
        <Button size="xs" data-slot="cc-guide-confirm" disabled={busy || !review.ready} onClick={() => act(() => confirmGuide(view.run, view.version))}>
          {t("recordNow")}
        </Button>
      </div>
    </div>
  );
};

export const GuideResultCard: FC<{ view: GuideView }> = ({ view }) => {
  const result = view.result!;
  return (
    <p data-slot="cc-guide-result" data-result={result.status} className="cc-ask-note">
      {[result.customer, result.total, result.docnum, result.reason_text].filter(Boolean).join(" · ")}
    </p>
  );
};
