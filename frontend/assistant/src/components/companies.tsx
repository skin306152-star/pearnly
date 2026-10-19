// 公司列表（侧栏「公司」，深链接 ?view=companies；只在 Cowork）：全所公司主档，一页 50 条，滚到底接着加载。
// 列表页样式照 Claude Code 的项目列表（设计文档 3.14 / 4.5）：内容宽 960 居中；标题 24/500；右上「排序方式 当前值 ⌄」与
// 「＋ 添加公司」主按钮（components/add-company.tsx）；搜索框 36 高；卡片网格：公司名、税号 · 分店 · 年度、这家的待处理数、
// 我在这家的最近活动。点一张卡打开公司页。
//
//   排序：最近活动（服务端的顺序：我有对话的按我最近的活动在前，其余按名称）/ 名称（已加载的按名称排）。空状态只有「＋ 添加公司」。
//   别处改名、删公司（pearnly.project 的 upsert / delete）时重取。样式只用 globals.css 的 .cc-* 与变量。

import { CheckIcon, ChevronDownIcon, Building2Icon, InboxIcon, LoaderIcon, PlusIcon, SearchIcon } from "lucide-react";
import { type FC, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { openAddCompany } from "@/components/add-company";
import { useOpenCompany } from "@/components/assistant-ui/projects";
import { DropdownMenu, menuOffset } from "@/components/ui/menu";
import { co, lang, t } from "@/i18n";
import { branchShort, type CompanyRow, forgetAnyCompany, formatWhen, listCompanies, usePendingCounts } from "@/lib/companies";
import { onProjectChange } from "@/lib/managed-agents/realtime";
import { shownError } from "@/lib/frappe";
import { Marquee } from "@/components/ui/marquee";
import { fixedPx } from "@/lib/css-var";

const PAGE = 50;
// 滚到离底这么远就取下一页（globals.css 的 --cc-observe-margin，建观察器时读；这是样式表还没生效时的回退，测试核对两边同值）
export const OBSERVE_MARGIN = 200;

/** 一行的第二行：税号 · 分店 · 年度。 */
export function companyLine(row: Pick<CompanyRow, "tax_id" | "branch" | "years">): string {
  return [row.tax_id ?? "", row.tax_id && row.branch ? branchShort(row.branch) : "", (row.years ?? []).join(" ")]
    .filter(Boolean)
    .join(" · ");
}

export type CompanySort = "recent" | "name";

/** 排序：recent = 服务端给的顺序；name = 按名称（按页面语言比较）。 */
export function sortCompanies<T extends Pick<CompanyRow, "title">>(rows: readonly T[], sort: CompanySort): T[] {
  if (sort === "recent") return [...rows];
  const collator = new Intl.Collator(lang, { sensitivity: "base", numeric: true });
  return [...rows].sort((a, b) => collator.compare(a.title, b.title));
}

const SORTS: Array<[CompanySort, () => string]> = [
  ["recent", () => co.sortRecent],
  ["name", () => co.sortName],
];

const SortMenu: FC<{ value: CompanySort; onChange: (sort: CompanySort) => void }> = ({ value, onChange }) => (
  <DropdownMenu.Root>
    <DropdownMenu.Trigger className="cc-btn-sm" data-slot="cc-company-sort">
      <span className="text-ink-3">{co.sortBy}</span>
      {SORTS.find(([sort]) => sort === value)?.[1]()}
      <ChevronDownIcon />
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal>
      <DropdownMenu.Content align="end" sideOffset={menuOffset()} className="cc-menu">
        {SORTS.map(([sort, label]) => (
          <DropdownMenu.Item key={sort} className="cc-menu-item" onSelect={() => onChange(sort)}>
            <span className="flex-1">{label()}</span>
            {sort === value && <CheckIcon />}
          </DropdownMenu.Item>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>
);

export const Companies: FC = () => {
  const openCompany = useOpenCompany();
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<CompanySort>("recent");
  const pending = usePendingCounts((s) => s.companies);
  const [rows, setRows] = useState<CompanyRow[] | null>(null);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const asked = useRef(0);
  const end = useRef<HTMLDivElement>(null);

  const load = useCallback(async (text: string, start: number) => {
    const mine = ++asked.current;
    setLoading(true);
    try {
      const page = await listCompanies(text.trim(), start, PAGE);
      if (mine !== asked.current) return;
      setRows((before) => (start ? [...(before ?? []), ...page] : page));
      setDone(page.length < PAGE);
      setError("");
    } catch (e) {
      if (mine !== asked.current) return;
      setRows((before) => before ?? []);
      setError(shownError(e));
    } finally {
      if (mine === asked.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(search, 0), search ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [search, load]);

  // 别处改名、删了公司：重取第一页
  useEffect(() => onProjectChange((change) => change.op !== "move" && void load(search, 0)), [search, load]);

  // 滚到底加载下一页
  useEffect(() => {
    const el = end.current;
    if (!el || done || loading || !rows?.length) return;
    const observer = new IntersectionObserver(([entry]) => entry?.isIntersecting && void load(search, rows.length), { rootMargin: `${fixedPx("--cc-observe-margin", OBSERVE_MARGIN)}px` });
    observer.observe(el);
    return () => observer.disconnect();
  }, [rows, done, loading, search, load]);

  // 按名称排：把剩下的页一次取完（边滚边加载时，新取到的会按名称插进已显示的中间）；出错停下
  useEffect(() => {
    if (sort !== "name" || done || loading || error || !rows) return;
    void load(search, rows.length);
  }, [sort, rows, done, loading, error, search, load]);

  const shown = useMemo(() => (rows ? sortCompanies(rows, sort) : null), [rows, sort]);

  const add = () =>
    openAddCompany((id) => {
      forgetAnyCompany();
      openCompany(id);
    });

  return (
    <div data-slot="cc-scroll" className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="cc-page">
        <div className="cc-page-head">
          <h1 className="cc-page-title"><Marquee className="cc-page-title-text">{co.companies}</Marquee></h1>
          {!!rows?.length && <SortMenu value={sort} onChange={setSort} />}
          <button type="button" className="cc-btn-sm" data-primary="" onClick={add}>
            <PlusIcon />
            {co.addCompany}
          </button>
        </div>
        {(search || !!rows?.length) && (
          <label className="cc-page-search">
            <SearchIcon />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label={t("search")}
              placeholder={t("search")}
            />
          </label>
        )}
        {error && <div role="alert" className="cc-alert mb-(--cc-space-16)">{error}</div>}
        {shown === null ? (
          <div className="cc-card-grid">
            {[0, 1, 2].map((i) => <div key={i} className="cc-grid-card animate-cc-pulse" />)}
          </div>
        ) : !shown.length && !search ? (
          <div className="cc-empty cc-enter">
            <Building2Icon />
            <button type="button" className="cc-btn" data-primary="" onClick={add}>
              <PlusIcon />
              {co.addCompany}
            </button>
          </div>
        ) : !shown.length ? (
          <div className="cc-empty-line">{t("noResults")}</div>
        ) : (
          <>
            <div className="cc-card-grid" data-slot="cc-company-rows">
              {shown.map((row) => (
                <button key={row.id} type="button" className="cc-grid-card cc-enter" onClick={() => openCompany(row.id)}>
                  <Marquee className="cc-grid-card-title">{row.title}</Marquee>
                  <Marquee className="truncate text-ink-3">{companyLine(row)}</Marquee>
                  <span className="cc-grid-card-foot">
                    {!!pending[row.id] && (
                      <span className="flex items-center gap-(--cc-space-4)" title={co.pending}>
                        <InboxIcon />
                        {pending[row.id]}
                      </span>
                    )}
                    {row.mine_last_active && <Marquee className="truncate">{formatWhen(row.mine_last_active)}</Marquee>}
                  </span>
                </button>
              ))}
            </div>
            <div ref={end} className="flex h-(--cc-control) items-center justify-center text-ink-3">
              {loading && <LoaderIcon className="cc-spinner size-(--cc-icon-xs)" />}
            </div>
          </>
        )}
      </div>
    </div>
  );
};
