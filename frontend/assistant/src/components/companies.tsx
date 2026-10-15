// 公司页（侧栏「公司」，深链接 ?view=companies；只在 Cowork）：全所公司主档，一页 50 条，滚到底接着加载。
//
//   顶部搜索框，右侧「＋ 添加公司」（components/add-company.tsx）；每行：名称、税号 · 分店、年度、我的对话数；点一行打开主档页。
//   我有对话的排在前（按我最近的活动），其余按名称（服务端排）。空状态只有「＋ 添加公司」。
//   别处改名、删公司（pearnly.project 的 upsert / delete）时重取。样式只用 globals.css 的 .cc-* 与变量。

import { Building2Icon, LoaderIcon, MessageSquareIcon, PlusIcon } from "lucide-react";
import { type FC, useCallback, useEffect, useRef, useState } from "react";
import { openAddCompany } from "@/components/add-company";
import { useOpenCompany } from "@/components/assistant-ui/projects";
import { co, t } from "@/i18n";
import { branchShort, type CompanyRow, forgetAnyCompany, listCompanies } from "@/lib/companies";
import { onProjectChange } from "@/lib/managed-agents/realtime";
import { shownError } from "@/lib/frappe";

const PAGE = 50;

/** 一行的第二行：税号 · 分店 · 年度。 */
export function companyLine(row: Pick<CompanyRow, "tax_id" | "branch" | "years">): string {
  return [row.tax_id ?? "", row.tax_id && row.branch ? branchShort(row.branch) : "", (row.years ?? []).join(" ")]
    .filter(Boolean)
    .join(" · ");
}

export const Companies: FC = () => {
  const openCompany = useOpenCompany();
  const [search, setSearch] = useState("");
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
    const observer = new IntersectionObserver(([entry]) => entry?.isIntersecting && void load(search, rows.length), { rootMargin: "200px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, [rows, done, loading, search, load]);

  const add = () =>
    openAddCompany((id) => {
      forgetAnyCompany();
      openCompany(id);
    });

  return (
    <div data-slot="cc-scroll" className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-(--cc-thread-w) px-4 pt-4 pb-10 md:pt-10">
        <div className="mb-6 flex items-center justify-between gap-3">
          <h1 className="cc-greeting text-3xl">{co.companies}</h1>
          <button type="button" className="cc-btn" data-primary="" onClick={add}>
            <PlusIcon className="size-4" />
            {co.addCompany}
          </button>
        </div>
        {(search || !!rows?.length) && (
          <label className="cc-search mb-4">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label={t("search")}
              placeholder={t("search")}
            />
          </label>
        )}
        {error && <div role="alert" className="mb-4 rounded-md bg-danger-soft px-3 py-2 text-danger text-sm">{error}</div>}
        {rows === null ? (
          <div className="flex flex-col gap-2">
            {[0, 1, 2].map((i) => <div key={i} className="cc-card h-16 animate-pulse" />)}
          </div>
        ) : !rows.length && !search ? (
          <div className="cc-card cc-enter flex flex-col items-center gap-3 px-6 py-12 text-center">
            <Building2Icon className="size-8 text-ink-3" />
            <button type="button" className="cc-btn" data-primary="" onClick={add}>
              <PlusIcon className="size-4" />
              {co.addCompany}
            </button>
          </div>
        ) : !rows.length ? (
          <div className="py-6 text-center text-ink-3 text-sm">{t("noResults")}</div>
        ) : (
          <div className="flex flex-col gap-2" data-slot="cc-company-rows">
            {rows.map((row) => (
              <button
                key={row.id}
                type="button"
                className="cc-card cc-card-hover cc-enter flex items-center gap-3 px-4 py-3 text-start"
                onClick={() => openCompany(row.id)}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-ink">{row.title}</span>
                  <span className="block truncate text-ink-3 text-sm">{companyLine(row)}</span>
                </span>
                {!!row.mine_count && (
                  <span className="flex shrink-0 items-center gap-1 text-ink-3 text-sm" title={co.conversations}>
                    <MessageSquareIcon className="size-3.5" />
                    {row.mine_count}
                  </span>
                )}
              </button>
            ))}
            <div ref={end} className="flex h-8 items-center justify-center text-ink-3">
              {loading && <LoaderIcon className="size-3.5 animate-spin" />}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
