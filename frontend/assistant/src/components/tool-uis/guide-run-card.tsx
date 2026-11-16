// 按表单的引导卡（引导式 P0 接线；服务端 assistant/guide/run.py）：一次引导在对话里是一张卡，按服务端此刻的状态画——
// 在问：这一轮要答的题（单选 / 金额多选 / 分摊 / 日期），已定的项一行灰字；复核：要办的全部摆出来，「写入」才办；办完：结果一行。
// 卡上的值都是服务端给的选项码或用户填的数，按键交回（`answerGuide`），页面不拼、不改、不经模型。标题、复核行名由服务端按本页语言翻好。
// 用标准组件（Button、RadioGroup、Input、Item 与 guide-inputs 里的金额 / 分摊 / 一格输入），样式走 globals.css 现有的卡片类。

import { type FC, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Item, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { ask, guide, t } from "@/i18n";
import { withReport } from "@/lib/caught";
import { type AmountRow, rowsToSend, sumMinor, fromMinor } from "@/lib/guide-inputs";
import {
  answerGuide, cancelGuide, confirmGuide, type GuideOption, type GuideQuestion, type GuideView, searchGuide, viewGuide,
} from "@/lib/guide-run";
import type { GuideRun } from "@/lib/managed-agents/reducer";
import { AmountsField, SplitField, ValueField } from "./guide-inputs";

const OPEN = new Set(["asking", "reviewing"]);
const WAIT_MS = 3000;

type Values = Record<string, unknown>;

const hasValue = (value: unknown) =>
  !(value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0));

/** 一题：按输入方式画，值交给上层。 */
const Question: FC<{
  run: string; question: GuideQuestion; value: unknown; answers: Values; disabled: boolean; returned?: string;
  onChange: (value: unknown) => void;
}> = ({ run, question, value, answers, disabled, returned, onChange }) => {
  const [options, setOptions] = useState<GuideOption[]>(question.options ?? []);
  const [txt, setTxt] = useState("");
  useEffect(() => setOptions(question.options ?? []), [question.options]);
  useEffect(() => {
    if (!question.search) return;
    const timer = setTimeout(() => {
      void searchGuide(run, question.key, txt).then((found) => setOptions(found.options ?? []))
        .catch(withReport(() => undefined, "guide-search"));
    }, 300);
    return () => clearTimeout(timer);
  }, [run, question.key, question.search, txt]);
  const total = useMemo(() => {
    // 分摊的合计 = 前面金额多选那一步填的合计（收款：各张发票本次收的合计）
    const rows = Object.values(answers).find((v) => Array.isArray(v) && v.length && typeof v[0] === "object") as AmountRow[] | undefined;
    return rows ? fromMinor(sumMinor(rows)) : "";
  }, [answers]);
  let body = null;
  if (question.input === "choice") {
    body = (
      <>
        {question.search ? <Input value={txt} disabled={disabled} onChange={(e) => setTxt(e.target.value)} aria-label={question.title} /> : null}
        <RadioGroup value={typeof value === "string" ? value : ""} onValueChange={onChange} disabled={disabled}>
          {options.map((option) => (
            <Item key={option.code} size="sm" asChild>
              <label>
                <RadioGroupItem value={option.code} />
                <ItemContent>
                  <ItemTitle>{option.name || option.code}</ItemTitle>
                  {option.note ? <ItemDescription>{option.note}</ItemDescription> : null}
                </ItemContent>
              </label>
            </Item>
          ))}
        </RadioGroup>
      </>
    );
  } else if (question.input === "amounts") {
    body = <AmountsField options={options} rows={(value as AmountRow[]) ?? []} disabled={disabled} onChange={onChange} />;
  } else if (question.input === "split") {
    body = <SplitField options={options} rows={(value as AmountRow[]) ?? []} total={total} disabled={disabled} onChange={onChange} />;
  } else if (["date", "month", "number", "text"].includes(question.input)) {
    body = (
      <ValueField kind={question.input as "date" | "month" | "number" | "text"} value={typeof value === "string" ? value : ""}
        label={question.title} disabled={disabled} onChange={onChange} />
    );
  }
  return (
    <div data-slot="cc-guide-question" data-key={question.key} className="cc-ui-questionnaire-question">
      <div className="cc-ask-q">{question.title}</div>
      {returned ? <p className="cc-ask-note">{returned}</p> : null}
      {(question.facts ?? []).map((fact) => <p key={fact} className="cc-ask-note">{fact}</p>)}
      {body}
    </div>
  );
};

/** 一次引导在对话里的卡。 */
export const GuideRunCard: FC<{ run: GuideRun }> = ({ run }) => {
  const [view, setView] = useState<GuideView | null>(null);
  const [values, setValues] = useState<Values>({});
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const load = useCallback(() => {
    void viewGuide(run.run).then(setView).catch(withReport(() => undefined, "guide-view"));
  }, [run.run]);
  useEffect(load, [load, run.version]);
  // 选项还在读（读回没到）：过一会儿再取这张卡
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const waiting = view?.card?.questions.some((q) => q.wait) || view?.result?.wait;
    if (waiting) timer.current = setTimeout(load, WAIT_MS);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [view, load]);
  useEffect(() => setValues({}), [view?.version]);
  const act = (call: () => Promise<GuideView>) => {
    setBusy(true);
    void call().then(setView).catch(withReport(() => undefined, "guide-act")).finally(() => setBusy(false));
  };
  if (!view) return null;
  const status = view.status;
  const label = view.business === "collection" ? guide.collection : view.business;
  if (!OPEN.has(status) && status !== "done" && status !== "failed" && status !== "confirmed") {
    return <div data-slot="cc-guide-run" data-status={status} className="cc-steps-card cc-ui-questionnaire-card"><div className="cc-ui-questionnaire-done">{label}</div></div>;
  }
  const card = view.card;
  const answers = card?.answers ?? {};
  const questions = card?.questions ?? [];
  const filled = questions.every((q) => !q.required || hasValue(values[q.key]));
  const send = () => {
    const given: Values = {};
    for (const q of questions) {
      const value = values[q.key];
      if (!hasValue(value)) continue;
      given[q.key] = q.input === "amounts" || q.input === "split" ? rowsToSend(value as AmountRow[]) : value;
    }
    act(() => answerGuide(view.run, given, view.version));
  };
  return (
    <div data-slot="cc-guide-run" data-status={status} data-business={view.business} className="cc-steps-card cc-ui-questionnaire-card">
      <div className="cc-ui-questionnaire-done">{label}</div>
      {status === "asking" ? (
        <>
          {(card?.decided ?? []).map((item) => (
            <p key={item.key} data-slot="cc-guide-decided" className="cc-ask-note">{item.title} · {item.value_label ?? String(item.value ?? "")}</p>
          ))}
          {questions.map((q) => (
            <Question key={q.key} run={view.run} question={q} value={values[q.key]} answers={{ ...answers, ...values }} disabled={busy}
              returned={card?.returned?.[q.key]?.text} onChange={(value) => setValues((s) => ({ ...s, [q.key]: value }))} />
          ))}
          <div className="cc-card-actions">
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => act(() => cancelGuide(view.run))}>{t("cancel")}</Button>
            <Button size="sm" disabled={busy || !filled || !questions.length} onClick={send}>{ask.submit}</Button>
          </div>
        </>
      ) : null}
      {status === "reviewing" && view.review ? (
        <div data-slot="cc-guide-review">
          {(view.review.lines ?? []).map((line) => (
            <p key={line.key} className="cc-ask-note"><span>{line.label}</span> · <span>{line.value}</span></p>
          ))}
          {(view.review.tables ?? []).map((table) => (
            <div key={table.key} className="cc-ask-changes">
              <div className="cc-ask-q">{table.label}</div>
              {table.rows.map((row) => (
                <p key={row.code} data-code={row.code}>{row.name || row.code}{row.note ? ` · ${row.note}` : ""} · {row.amount}</p>
              ))}
            </div>
          ))}
          <p data-slot="cc-guide-total">{guide.total} · {view.review.total}</p>
          {view.review.problem_text ? <p className="cc-ask-note">{view.review.problem_text}</p> : null}
          <div className="cc-card-actions">
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => act(() => cancelGuide(view.run))}>{t("cancel")}</Button>
            <Button size="sm" disabled={busy || !view.review.ready} onClick={() => act(() => confirmGuide(view.run, view.version))}>{t("recordNow")}</Button>
          </div>
        </div>
      ) : null}
      {view.result ? (
        <p data-slot="cc-guide-result" data-result={view.result.status} className="cc-ask-note">
          {view.result.customer ?? ""}{view.result.total ? ` · ${view.result.total}` : ""}{view.result.docnum ? ` · ${view.result.docnum}` : ""}
          {view.result.reason_text ? ` · ${view.result.reason_text}` : ""}
        </p>
      ) : null}
    </div>
  );
};
