// 处理月份与分店的胶囊（主档页输入框、对话输入框的下巴共用；下巴里是幽灵按钮 .cc-chin-btn、菜单向上开；
// 手机新对话放在问候下面、菜单向下开，手机已有对话在「…」的子菜单里，各项用同一份 PeriodChoices / BranchChoices）：
//
//   月份 — 本月往前 12 个月 + 「不指定」；新对话默认上个月（页面按这台设备的日期定，服务端只核对形状）
//   分店 — 只在这家公司有不止一个分店时出现；默认「不指定」（CC 按票面的买方分店选账套，看不出时问一次）
//   账套 — 新建页（还没有会话）的本对话账套（lib/conversation-ledger 的 useDraftLedgerOf）：这家这个月可选的几本，服务端的默认
//          预选、可改，建会话时一起交上；这家没有本对话账套时不出现。已有对话在右栏公司卡里改（session-panel 的 LedgerRow）
//
// 用原生下拉菜单（radix），样式只用 globals.css 的 .cc-chip-btn / .cc-chin-btn / .cc-menu 与变量。

import { BookOpenIcon, CalendarIcon, CheckIcon, ChevronDownIcon, StoreIcon } from "lucide-react";
import { DropdownMenu, menuOffset } from "@/components/ui/menu";
import type { FC, ReactNode } from "react";
import { co } from "@/i18n";
import { type Branch, branchShort, monthLabel, recentMonths } from "@/lib/companies";
import { ledgerLabel, type LedgerView } from "@/lib/conversation-ledger";
import { Marquee } from "@/components/ui/marquee";

/** 下拉里的一项（选中的带勾）：月份、分店，右栏的本对话账套（session-panel 的 LedgerRow）共用。 */
export const Choice: FC<{ chosen: boolean; onSelect: () => void; children: ReactNode }> = ({ chosen, onSelect, children }) => (
  <DropdownMenu.Item className="cc-menu-item" onSelect={onSelect}>
    <Marquee className="min-w-0 flex-1 truncate">{children}</Marquee>
    {chosen && <CheckIcon />}
  </DropdownMenu.Item>
);

/** 胶囊的外观与菜单方向：默认胶囊、菜单向下；下巴里传 look="cc-chin-btn"、side="top"（放不下由菜单包装翻面）。 */
type ChipLook = { look?: "cc-chip-btn" | "cc-chin-btn"; side?: "top" | "bottom" };

/** 月份胶囊：value 是 YYYY-MM（"" = 不指定）；allowNone={false}（公司页的月结台账）不列「不指定」。 */
export const PeriodChip: FC<{ value: string; onChange: (period: string) => void; disabled?: boolean; allowNone?: boolean } & ChipLook> = ({
  value,
  onChange,
  disabled,
  allowNone = true,
  look = "cc-chip-btn",
  side,
}) => {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild disabled={disabled}>
        <button type="button" className={look} data-slot="cc-period-chip" aria-label={co.period} title={co.period}>
          <CalendarIcon />
          <Marquee className="truncate">{monthLabel(value)}</Marquee>
          <ChevronDownIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side={side} align="start" sideOffset={menuOffset()} className="cc-menu min-w-(--cc-menu-w-sm) [--cc-menu-max:var(--cc-menu-max-list)]">
          <PeriodChoices value={value} onChange={onChange} allowNone={allowNone} />
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};

/** 月份的各项（胶囊的菜单、手机对话页「…」里的「处理月份」子菜单共用）。 */
export const PeriodChoices: FC<{ value: string; onChange: (period: string) => void; allowNone?: boolean }> = ({
  value,
  onChange,
  allowNone = true,
}) => {
  const months = recentMonths();
  // 对话原来处理的月份不在最近 12 个月里：照样列出来（不改它）
  if (value && !months.includes(value)) months.push(value);
  return (
    <>
      {months.map((month) => (
        <Choice key={month} chosen={month === value} onSelect={() => month !== value && onChange(month)}>
          {monthLabel(month)}
        </Choice>
      ))}
      {allowNone && (
        <>
          <DropdownMenu.Separator className="cc-menu-sep" />
          <Choice chosen={!value} onSelect={() => value && onChange("")}>
            {co.noPeriod}
          </Choice>
        </>
      )}
    </>
  );
};

/** 分店名：服务端给的显示名，没有就「总公司」/「分店 00001」。 */
export const branchName = (branch: Pick<Branch, "branch" | "label">) => branch.label || branchShort(branch.branch);

/** 分店胶囊：只有一个分店（或没有分店信息）时不出现。value 是 5 位分店号（"" = 不指定）。 */
export const BranchChip: FC<
  { branches: readonly Branch[]; value: string; onChange: (scope: string) => void; disabled?: boolean } & ChipLook
> = ({ branches, value, onChange, disabled, look = "cc-chip-btn", side }) => {
  if (branches.length < 2) return null;
  const chosen = branches.find((b) => b.branch === value);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild disabled={disabled}>
        <button type="button" className={look} data-slot="cc-branch-chip" aria-label={co.branch} title={co.branch}>
          <StoreIcon />
          <Marquee className="truncate">{chosen ? branchName(chosen) : value ? branchShort(value) : co.noBranch}</Marquee>
          <ChevronDownIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side={side} align="start" sideOffset={menuOffset()} className="cc-menu min-w-(--cc-menu-w-sm) [--cc-menu-max:var(--cc-menu-max-list)]">
          <BranchChoices branches={branches} value={value} onChange={onChange} />
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};

/** 分店的各项（胶囊的菜单、手机对话页「…」里的「分店」子菜单共用）。 */
export const BranchChoices: FC<{ branches: readonly Branch[]; value: string; onChange: (scope: string) => void }> = ({
  branches,
  value,
  onChange,
}) => (
  <>
    {branches.map((b) => (
      <Choice key={b.branch} chosen={b.branch === value} onSelect={() => b.branch !== value && onChange(b.branch)}>
        {branchName(b)}
      </Choice>
    ))}
    <DropdownMenu.Separator className="cc-menu-sep" />
    <Choice chosen={!value} onSelect={() => value && onChange("")}>
      {co.noBranch}
    </Choice>
  </>
);

/** 账套胶囊（新建页）：entry 是服务端给的可选与当前那本（没读到、这家没有本对话账套时不出现）；还没定时写「ชุดบัญชี」本身，
 *  不加说明文字。 */
// map: draft-ledger-picker
export const LedgerChip: FC<{ entry: LedgerView | null; onChoose: (id: string) => void } & ChipLook> = ({
  entry,
  onChoose,
  look = "cc-chip-btn",
  side,
}) => {
  if (!entry?.options.length) return null;
  const label = ledgerLabel(entry);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button type="button" className={look} data-slot="cc-ledger-chip" aria-label={co.accountSets} title={label || co.accountSets}>
          <BookOpenIcon />
          <Marquee className="truncate">{label || co.accountSets}</Marquee>
          <ChevronDownIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side={side} align="start" sideOffset={menuOffset()} className="cc-menu min-w-(--cc-menu-w-sm) [--cc-menu-max:var(--cc-menu-max-list)]">
          {entry.options.map((item) => (
            <Choice key={item.id} chosen={item.id === entry.current} onSelect={() => item.id !== entry.current && onChoose(item.id)}>
              {item.label}
            </Choice>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};
