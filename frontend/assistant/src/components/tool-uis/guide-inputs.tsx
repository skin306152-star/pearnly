// 引导卡上的各种输入（总设计 docs/work/2026-11-14-cc-guided-master-design.md 2.2）：金额多选（勾发票、每张可改金额）、
// 分摊（一个总额分到几种方式上）、一格输入（月份 / 日期 / 数字 / 文字）。取值与核对在 lib/guide-inputs.ts（纯函数，规则同服务端），
// 这里只管显示：标准组件库 Item（一行一项，电脑手机同一种排法，不用表格——手机上表格太宽）+ Checkbox + Input + Button。
// 标红不拦：不对的那一格 aria-invalid，交不交由题卡的「下一步 / 提交」按服务端的答复定。
import { PaperclipIcon } from "lucide-react";
import { type FC, useEffect, useId, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item";
import { QuestionnaireChoice, QuestionnaireChoiceDescription, QuestionnaireChoices } from "@/components/ui/questionnaire";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Spinner } from "@/components/ui/spinner";
import { co, guide, lang, t } from "@/i18n";
import { reportCaught, withReport } from "@/lib/caught";
import { shownError } from "@/lib/frappe";
import { type GuideSub, guideFiles } from "@/lib/guide-run";
import { ACCEPT, refusalOf } from "@/lib/managed-agents/attachments";
import {
  type AmountOption, type AmountRow, type ValueKind, amountsProblem, fillRest, formatMinor, setAmount, splitRemainder, sumMinor,
  toMinor, toggleRow, valueOk,
} from "@/lib/guide-inputs";

const optionName = (option: AmountOption) => option.name || option.code;

/** 合计那一行（金额多选与分摊共用）：左边「合计」，右边数；分摊另带还差 / 多出多少。 */
const TotalLine: FC<{ total: bigint; rest?: bigint }> = ({ total, rest }) => (
  <Item size="sm" data-slot="cc-guide-total">
    <ItemContent>
      <ItemTitle>{guide.total}</ItemTitle>
      {rest !== undefined && rest !== 0n ? (
        <ItemDescription data-rest={rest > 0n ? "left" : "over"}>
          {rest > 0n ? guide.remaining(formatMinor(rest, lang)) : guide.over(formatMinor(-rest, lang))}
        </ItemDescription>
      ) : null}
    </ItemContent>
    <ItemActions>
      <span className="tabular-nums">{formatMinor(total, lang)}</span>
    </ItemActions>
  </Item>
);

/** 金额多选：勾上一行填它的默认数（发票的未收余额，同网页收款弹窗 collection-new.js 的 fill_checked），可改；交回 [{code, amount}]。
 *  zero（N4 盘点记数）：行上可以填 0。 */
export const AmountsField: FC<{
  options: readonly AmountOption[];
  rows: readonly AmountRow[];
  disabled?: boolean;
  zero?: boolean;
  onChange: (rows: AmountRow[]) => void;
}> = ({ options, rows, disabled, zero, onChange }) => {
  const id = useId();
  const picked = new Map(rows.map((row) => [row.code, row.amount]));
  const wrong = amountsProblem(options, rows, zero);
  return (
    <ItemGroup data-slot="cc-guide-amounts" className="cc-guide-body">
      {options.map((option) => {
        const checked = picked.has(option.code);
        const boxId = `${id}-${option.code}`;
        return (
          <Item key={option.code} size="sm" data-checked={checked || undefined}>
            <ItemMedia>
              <Checkbox id={boxId} checked={checked} disabled={disabled}
                onCheckedChange={(value) => onChange(toggleRow(options, rows, option.code, value === true))} />
            </ItemMedia>
            <ItemContent>
              <ItemTitle>
                <label htmlFor={boxId}>{optionName(option)}</label>
              </ItemTitle>
              {option.note ? <ItemDescription>{option.note}</ItemDescription> : null}
            </ItemContent>
            <ItemActions>
              <Input className="cc-guide-amount w-(--cc-guide-amount-w) shrink-0" inputMode="decimal" aria-label={`${guide.amount} ${optionName(option)}`}
                disabled={disabled || !checked} value={picked.get(option.code) ?? ""}
                placeholder={toMinor(option.max ?? option.amount) !== null ? formatMinor(toMinor(option.max ?? option.amount) as bigint, lang) : undefined}
                aria-invalid={(wrong.code === option.code && wrong.problem !== "") || undefined}
                onChange={(event) => onChange(setAmount(rows, option.code, event.currentTarget.value))} />
            </ItemActions>
          </Item>
        );
      })}
      <TotalLine total={sumMinor(rows)} />
    </ItemGroup>
  );
};

/** 分摊：每种方式一格金额，合计要等于总额；空着的一格在还有余额时出「填入余额」。 */
export const SplitField: FC<{
  options: readonly AmountOption[];
  rows: readonly AmountRow[];
  total: string;
  disabled?: boolean;
  onChange: (rows: AmountRow[]) => void;
}> = ({ options, rows, total, disabled, onChange }) => {
  const byCode = new Map(rows.map((row) => [row.code, row.amount]));
  const all = options.map((option) => ({ code: option.code, amount: byCode.get(option.code) ?? "" }));
  const filled = all.filter((row) => row.amount.trim() !== "");
  const rest = splitRemainder(filled, total);
  return (
    <ItemGroup data-slot="cc-guide-split" className="cc-guide-body">
      {options.map((option) => {
        const value = byCode.get(option.code) ?? "";
        const bad = value.trim() !== "" && (toMinor(value) ?? 0n) <= 0n;
        return (
          <Item key={option.code} size="sm">
            <ItemContent>
              <ItemTitle>{optionName(option)}</ItemTitle>
              {option.note ? <ItemDescription>{option.note}</ItemDescription> : null}
            </ItemContent>
            <ItemActions>
              {value.trim() === "" && rest > 0n && !disabled ? (
                <Button variant="ghost" size="xs" type="button" onClick={() => onChange(fillRest(all, total, option.code))}>
                  {guide.fillRest}
                </Button>
              ) : null}
              <Input className="cc-guide-amount w-(--cc-guide-amount-w) shrink-0" inputMode="decimal" aria-label={`${guide.amount} ${optionName(option)}`}
                disabled={disabled} value={value} aria-invalid={bad || undefined}
                onChange={(event) => onChange(setAmount(all, option.code, event.currentTarget.value))} />
            </ItemActions>
          </Item>
        );
      })}
      <TotalLine total={sumMinor(filled)} rest={rest} />
    </ItemGroup>
  );
};

const INPUT_TYPE: Record<ValueKind, string> = { month: "month", date: "date", time: "time", number: "text", text: "text" };

/** 一格输入：月份 / 日期 / 时刻用浏览器自带的选择器；数字用小数键盘；填了但不对的标红。 */
export const ValueField: FC<{ kind: ValueKind; value: string; label: string; disabled?: boolean; onChange: (value: string) => void }> = ({
  kind, value, label, disabled, onChange,
}) => (
  <Input data-slot="cc-guide-value" type={INPUT_TYPE[kind]} inputMode={kind === "number" ? "decimal" : undefined} aria-label={label}
    disabled={disabled} value={value} aria-invalid={(value !== "" && !valueOk(kind, value)) || undefined}
    onChange={(event) => onChange(event.currentTarget.value)} />
);

// ── 单选 / 多选（可服务端搜索）、交文件、子题 ─────────────────────────────────────────────────────────────

/** 选项行：问卷部件的通栏行（圆点 / 复选框 + 名字 + 推荐徽章 + 灰色说明），同提问卡。 */
const ChoiceRows: FC<{
  options: readonly ChoiceItem[];
  picked: readonly string[];
  multi: boolean;
  recommended?: string;
  disabled?: boolean;
  label: string;
  onToggle: (code: string) => void;
}> = ({ options, picked, multi, recommended, disabled, label, onToggle }) => (
  <QuestionnaireChoices aria-label={label}>
    {options.map((option) => (
      <QuestionnaireChoice key={option.code} value={option.code} checked={picked.includes(option.code)} disabled={disabled}
        data-slot="cc-guide-option" data-code={option.code} onChange={() => onToggle(option.code)}>
        <span className="flex flex-wrap items-center gap-(--cc-space-8)">
          <span className="cc-askq-label">{option.name || option.code}</span>
          {!multi && recommended === option.code && <Badge variant="secondary">{guide.recommended}</Badge>}
        </span>
        {option.note && <QuestionnaireChoiceDescription className="cc-askq-desc">{option.note}</QuestionnaireChoiceDescription>}
      </QuestionnaireChoice>
    ))}
  </QuestionnaireChoices>
);

export type ChoiceItem = { code: string; name?: string; note?: string; amount?: string; max?: string };
export type SearchFn = (txt: string, start: number) => Promise<{ options: ChoiceItem[]; more: boolean }>;

/** 选项来源：题目自带的一页；带 search 的打字后去服务端搜（一页一页取，「显示更多」接着取）。选中过的记住名字（搜别的字时仍显示在最上面）。
 *  只交选项码，不交显示的字。 */
/** 先列几条（同提问卡「其他」：其余「显示更多」，本页已取到的先放出，不够再向服务端取下一页）。 */
export const FIRST_ROWS = 5;

export function useOptions(first: readonly ChoiceItem[], firstMore: boolean, search: SearchFn | undefined, picked: readonly string[]) {
  const [txt, setTxtRaw] = useState("");
  const [shown, setShown] = useState(FIRST_ROWS);
  const setTxt = (next: string) => { setTxtRaw(next); setShown(FIRST_ROWS); };
  const [page, setPage] = useState<{ options: ChoiceItem[]; more: boolean }>({ options: [...first], more: firstMore });
  const seen = useRef(new Map<string, ChoiceItem>());
  for (const option of first) seen.current.set(option.code, option);
  for (const option of page.options) seen.current.set(option.code, option);
  const firstKey = first.map((o) => o.code).join("\n");
  useEffect(() => {
    if (!search) return setPage({ options: [...first], more: firstMore });
    if (!txt) return setPage({ options: [...first], more: firstMore });
    let alive = true;
    const timer = window.setTimeout(() => void search(txt.trim(), 0).then((found) => alive && setPage(found), withReport(undefined, "guide-search")), 250);
    return () => { alive = false; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [txt, firstKey]);
  const more = () => {
    if (search && page.more && page.options.length <= shown + FIRST_ROWS) {
      void search(txt.trim(), page.options.length).then(
        (found) => setPage((before) => ({ options: [...before.options, ...found.options], more: found.more })), withReport(undefined, "guide-search"));
    }
    setShown((n) => n + FIRST_ROWS);
  };
  const visible = page.options.slice(0, shown);
  const listed = visible.map((o) => o.code);
  const pinned = picked.filter((code) => !listed.includes(code)).map((code) => seen.current.get(code) ?? { code });
  return { txt, setTxt, options: [...pinned, ...visible], more: page.options.length > shown || (!!search && page.more), loadMore: more, searched: !!txt };
}

/** 搜索框（选项多、服务端可搜的题）：打字即搜，结果替换下面的选项。 */
const SearchBox: FC<{ value: string; label: string; disabled?: boolean; onChange: (txt: string) => void }> = ({ value, label, disabled, onChange }) => (
  <div className="cc-guide-body">
    <Input data-slot="cc-guide-search" type="search" value={value} disabled={disabled} placeholder={t("search")} aria-label={`${t("search")} ${label}`}
      onChange={(event) => onChange(event.currentTarget.value)} />
  </div>
);

const MoreButton: FC<{ onClick: () => void }> = ({ onClick }) => (
  <Button type="button" variant="ghost" size="sm" className="cc-show-more justify-start rounded-none" data-slot="cc-guide-more" onClick={onClick}>
    {co.showMore}
  </Button>
);

/** 单选 / 多选：交回码（单选一个字符串，多选一组）。recommended：怎么办（how）的题后台推荐的那一项，带「推荐」徽章。 */
export const ChoiceField: FC<{
  options: readonly ChoiceItem[];
  more?: boolean;
  search?: SearchFn;
  multi: boolean;
  value: string | string[] | undefined;
  recommended?: string;
  label: string;
  disabled?: boolean;
  onChange: (value: string | string[]) => void;
}> = ({ options, more = false, search, multi, value, recommended, label, disabled, onChange }) => {
  const picked = Array.isArray(value) ? value : value ? [value] : [];
  const found = useOptions(options, more, search, picked);
  const toggle = (code: string) => {
    if (!multi) return onChange(code);
    onChange(picked.includes(code) ? picked.filter((c) => c !== code) : [...picked, code]);
  };
  return (
    <div className="flex min-w-0 flex-col" data-slot="cc-guide-choice">
      {search && <SearchBox value={found.txt} label={label} disabled={disabled} onChange={found.setTxt} />}
      <ChoiceRows options={found.options} picked={picked} multi={multi} recommended={recommended} disabled={disabled} label={label} onToggle={toggle} />
      {found.searched && !found.options.length && <div className="cc-menu-label">{t("noResults")}</div>}
      {found.more && <MoreButton onClick={found.loadMore} />}
    </div>
  );
};

/** 金额多选带搜索（N4 连续录多行：搜物料、勾上、填数量）：勾过的行一直留在最上面。 */
export const SearchAmountsField: FC<{
  options: readonly AmountOption[];
  more?: boolean;
  search?: SearchFn;
  rows: readonly AmountRow[];
  zero?: boolean;
  label: string;
  disabled?: boolean;
  onChange: (rows: AmountRow[]) => void;
}> = ({ options, more = false, search, rows, zero, label, disabled, onChange }) => {
  const found = useOptions(options, more, search, rows.map((row) => row.code));
  return (
    <div className="flex min-w-0 flex-col">
      {search && <SearchBox value={found.txt} label={label} disabled={disabled} onChange={found.setTxt} />}
      <AmountsField options={found.options} rows={rows} zero={zero} disabled={disabled} onChange={onChange} />
      {found.searched && !found.options.length && <div className="cc-menu-label">{t("noResults")}</div>}
      {found.more && <MoreButton onClick={found.loadMore} />}
    </div>
  );
};

/** 交文件：列本对话已交过的文件（勾选），也可当场添加（传进本对话，勾上）；交回文件 id 列表。登记归这次引导由服务端在作答时做。 */
export const FileField: FC<{
  sessionId: string | null;
  value: readonly string[];
  label: string;
  disabled?: boolean;
  onChange: (ids: string[]) => void;
}> = ({ sessionId, value, label, disabled, onChange }) => {
  const [listed, setListed] = useState<{ id: string; filename: string }[]>([]);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const pick = useRef<HTMLInputElement>(null);
  const source = useMemo(() => guideFiles(sessionId), [sessionId]);
  useEffect(() => {
    if (!source) return;
    let alive = true;
    void source.list().then((rows) => alive && setListed(rows), withReport(undefined, "guide-files"));
    return () => { alive = false; };
  }, [source]);
  const add = async (chosen: FileList | null) => {
    if (!source || !chosen?.length) return;
    setFailure("");
    setBusy(true);
    const ids = [...value];
    try {
      for (const file of Array.from(chosen)) {
        const refusal = refusalOf(file);
        if (refusal) { setFailure(refusal); continue; }
        const stored = await source.upload(file);
        setListed((rows) => [...rows.filter((row) => row.id !== stored.fileId), { id: stored.fileId, filename: stored.filename }]);
        if (!ids.includes(stored.fileId)) ids.push(stored.fileId);
      }
    } catch (err) {
      reportCaught(err, "guide-upload");
      setFailure(shownError(err));
    } finally {
      setBusy(false);
      onChange(ids);
    }
  };
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);
  return (
    <div className="cc-guide-body flex min-w-0 flex-col gap-(--cc-space-8)" data-slot="cc-guide-files">
      {listed.length > 0 && (
        <ChoiceRows options={listed.map((row) => ({ code: row.id, name: row.filename }))} picked={[...value]} multi label={label}
          disabled={disabled || busy} onToggle={toggle} />
      )}
      <Input ref={pick} type="file" multiple accept={ACCEPT} hidden onChange={(event) => { void add(event.currentTarget.files); event.currentTarget.value = ""; }} />
      <Button type="button" variant="outline" size="sm" className="w-fit" disabled={disabled || busy || !source} data-slot="cc-guide-add-file"
        onClick={() => pick.current?.click()}>
        {busy ? <Spinner /> : <PaperclipIcon />}
        {t("attach")}
      </Button>
      {failure && <Alert variant="destructive"><AlertDescription>{failure}</AlertDescription></Alert>}
    </div>
  );
};

/** 子题里的选项（一张卡里几组）：标准 RadioGroup / Checkbox 一行一项，不用问卷部件（问卷部件一题一组）。 */
const PlainChoices: FC<{
  options: readonly { code: string; name?: string }[];
  picked: readonly string[];
  multi: boolean;
  label: string;
  disabled?: boolean;
  onToggle: (code: string) => void;
}> = ({ options, picked, multi, label, disabled, onToggle }) => {
  const id = useId();
  if (!multi) {
    return (
      <RadioGroup aria-label={label} value={picked[0] ?? ""} disabled={disabled} onValueChange={onToggle}>
        {options.map((option) => (
          <Item key={option.code} size="sm" asChild>
            <label>
              <RadioGroupItem value={option.code} />
              <ItemContent><ItemTitle>{option.name || option.code}</ItemTitle></ItemContent>
            </label>
          </Item>
        ))}
      </RadioGroup>
    );
  }
  return (
    <ItemGroup aria-label={label}>
      {options.map((option) => (
        <Item key={option.code} size="sm">
          <ItemMedia>
            <Checkbox id={`${id}-${option.code}`} checked={picked.includes(option.code)} disabled={disabled} onCheckedChange={() => onToggle(option.code)} />
          </ItemMedia>
          <ItemContent><ItemTitle><label htmlFor={`${id}-${option.code}`}>{option.name || option.code}</label></ItemTitle></ItemContent>
        </Item>
      ))}
    </ItemGroup>
  );
};

/** 子题（N1a / N1b）：一组小题一次答，值 = {子题 key: 值}。 */
export const SubQuestionsField: FC<{
  questions: readonly GuideSub[];
  value: Record<string, unknown>;
  disabled?: boolean;
  onChange: (value: Record<string, unknown>) => void;
}> = ({ questions, value, disabled, onChange }) => (
  <div className="cc-guide-body flex min-w-0 flex-col gap-(--cc-space-12)" data-slot="cc-guide-subs">
    {questions.map((sub) => {
      const set = (next: unknown) => onChange({ ...value, [sub.key]: next });
      const current = value[sub.key];
      return (
        <div key={sub.key} className="flex min-w-0 flex-col gap-(--cc-space-4)" data-slot="cc-guide-sub" data-key={sub.key}>
          <div className="cc-askq-label">{sub.title}</div>
          {(sub.facts ?? []).map((fact) => <p key={fact} className="cc-askq-desc">{fact}</p>)}
          {sub.input === "choice" || sub.input === "multi" ? (
            <PlainChoices options={sub.options ?? []} multi={sub.input === "multi"} label={sub.title} disabled={disabled}
              picked={Array.isArray(current) ? current : typeof current === "string" && current ? [current] : []}
              onToggle={(code) => {
                if (sub.input === "choice") return set(code);
                const list = Array.isArray(current) ? (current as string[]) : [];
                set(list.includes(code) ? list.filter((c) => c !== code) : [...list, code]);
              }} />
          ) : (
            <ValueField kind={sub.input} label={sub.title} disabled={disabled} value={typeof current === "string" ? current : ""} onChange={set} />
          )}
        </div>
      );
    })}
  </div>
);
