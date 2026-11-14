// 引导卡上「不是单选」的几种输入（总设计 docs/work/2026-11-14-cc-guided-master-design.md 2.2）：金额多选（勾发票、每张可改金额）、
// 分摊（一个总额分到几种方式上）、一格输入（月份 / 日期 / 数字 / 文字）。取值与核对在 lib/guide-inputs.ts（纯函数，规则同服务端），
// 这里只管显示：标准组件库 Item（一行一项，电脑手机同一种排法，不用表格——手机上表格太宽）+ Checkbox + Input + Button。
// 标红不拦：不对的那一格 aria-invalid，交不交由题卡的「下一步 / 提交」按服务端的答复定。
import { type FC, useId } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item";
import { guide, lang } from "@/i18n";
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

/** 金额多选：勾上一行填它的默认数（发票的未收余额），可改；交回 [{code, amount}]。 */
export const AmountsField: FC<{
  options: readonly AmountOption[];
  rows: readonly AmountRow[];
  disabled?: boolean;
  onChange: (rows: AmountRow[]) => void;
}> = ({ options, rows, disabled, onChange }) => {
  const id = useId();
  const picked = new Map(rows.map((row) => [row.code, row.amount]));
  const wrong = amountsProblem(options, rows);
  return (
    <ItemGroup data-slot="cc-guide-amounts">
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
              <Input className="cc-guide-amount" inputMode="decimal" aria-label={`${guide.amount} ${optionName(option)}`}
                disabled={disabled || !checked} value={picked.get(option.code) ?? ""}
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
    <ItemGroup data-slot="cc-guide-split">
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
              <Input className="cc-guide-amount" inputMode="decimal" aria-label={`${guide.amount} ${optionName(option)}`}
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

const INPUT_TYPE: Record<ValueKind, string> = { month: "month", date: "date", number: "text", text: "text" };

/** 一格输入：月份 / 日期用浏览器自带的选择器；数字用小数键盘；填了但不对的标红。 */
export const ValueField: FC<{ kind: ValueKind; value: string; label: string; disabled?: boolean; onChange: (value: string) => void }> = ({
  kind, value, label, disabled, onChange,
}) => (
  <Input data-slot="cc-guide-value" type={INPUT_TYPE[kind]} inputMode={kind === "number" ? "decimal" : undefined} aria-label={label}
    disabled={disabled} value={value} aria-invalid={(value !== "" && !valueOk(kind, value)) || undefined}
    onChange={(event) => onChange(event.currentTarget.value)} />
);
