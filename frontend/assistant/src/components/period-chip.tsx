// 处理月份与分店的胶囊（主档页输入框、新对话的公司条、对话顶栏共用）：
//
//   月份 — 本月往前 12 个月 + 「不指定」；新对话默认上个月（页面按这台设备的日期定，服务端只核对形状）
//   分店 — 只在这家公司有不止一个分店时出现；默认「不指定」（CC 按票面的买方分店选账套，看不出时问一次）
//
// 用原生下拉菜单（radix），样式只用 globals.css 的 .cc-chip-btn / .cc-menu 与变量。

import { CalendarIcon, CheckIcon, ChevronDownIcon, StoreIcon } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import type { FC, ReactNode } from "react";
import { co } from "@/i18n";
import { type Branch, branchShort, monthLabel, recentMonths } from "@/lib/companies";

const Choice: FC<{ chosen: boolean; onSelect: () => void; children: ReactNode }> = ({ chosen, onSelect, children }) => (
  <DropdownMenu.Item className="cc-menu-item" onSelect={onSelect}>
    <span className="min-w-0 flex-1 truncate">{children}</span>
    {chosen && <CheckIcon />}
  </DropdownMenu.Item>
);

/** 月份胶囊：value 是 YYYY-MM（"" = 不指定）。 */
export const PeriodChip: FC<{ value: string; onChange: (period: string) => void; disabled?: boolean }> = ({ value, onChange, disabled }) => {
  const months = recentMonths();
  // 对话原来处理的月份不在最近 12 个月里：照样列出来（不改它）
  if (value && !months.includes(value)) months.push(value);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild disabled={disabled}>
        <button type="button" className="cc-chip-btn" data-slot="cc-period-chip" aria-label={co.period} title={co.period}>
          <CalendarIcon />
          <span className="truncate">{monthLabel(value)}</span>
          <ChevronDownIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="start" sideOffset={6} className="cc-menu !min-w-40 max-h-80 overflow-y-auto">
          {months.map((month) => (
            <Choice key={month} chosen={month === value} onSelect={() => month !== value && onChange(month)}>
              {monthLabel(month)}
            </Choice>
          ))}
          <DropdownMenu.Separator className="cc-menu-sep" />
          <Choice chosen={!value} onSelect={() => value && onChange("")}>
            {co.noPeriod}
          </Choice>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};

/** 分店名：服务端给的显示名，没有就「总公司」/「分店 00001」。 */
export const branchName = (branch: Pick<Branch, "branch" | "label">) => branch.label || branchShort(branch.branch);

/** 分店胶囊：只有一个分店（或没有分店信息）时不出现。value 是 5 位分店号（"" = 不指定）。 */
export const BranchChip: FC<{ branches: readonly Branch[]; value: string; onChange: (scope: string) => void; disabled?: boolean }> = ({
  branches,
  value,
  onChange,
  disabled,
}) => {
  if (branches.length < 2) return null;
  const chosen = branches.find((b) => b.branch === value);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild disabled={disabled}>
        <button type="button" className="cc-chip-btn" data-slot="cc-branch-chip" aria-label={co.branch} title={co.branch}>
          <StoreIcon />
          <span className="truncate">{chosen ? branchName(chosen) : value ? branchShort(value) : co.noBranch}</span>
          <ChevronDownIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="start" sideOffset={6} className="cc-menu !min-w-40 max-h-80 overflow-y-auto">
          {branches.map((b) => (
            <Choice key={b.branch} chosen={b.branch === value} onSelect={() => b.branch !== value && onChange(b.branch)}>
              {branchName(b)}
            </Choice>
          ))}
          <DropdownMenu.Separator className="cc-menu-sep" />
          <Choice chosen={!value} onSelect={() => value && onChange("")}>
            {co.noBranch}
          </Choice>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};
