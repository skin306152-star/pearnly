// 添加公司（公司页「＋ 添加公司」、新对话公司条里的「添加公司…」）：原生对话框，搜索 + 账套目录里的候选。
//
//   候选来自服务端读的账套目录（companies.api.candidates，按公司身份合并；不调电脑、不扣费）。页面只交「哪条连接的哪份账套」，
//   名称、税号由服务端从目录算；已添加的行尾「已添加」，点了直接打开那家。目录还没读到（没有候选）时只给「刷新账套」。
//   账套里没有的公司这一批不能建，也不做一次加多家。
//
// 打开方式：openAddCompany(then, title?)；then 收到公司编号（默认打开主档页）。title：定时任务总览「＋」先选公司时
// 叫「选择公司」（选已添加的或新加一家都行）。对话框在 assistant.tsx 里挂一次。

import { LoaderIcon, RefreshCwIcon, XIcon } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { type FC, useEffect, useRef, useState } from "react";
import { co, t } from "@/i18n";
import { create } from "zustand";
import { branchShort, type Candidate, createCompany, listCandidates, refreshCatalogs, requestKey } from "@/lib/companies";
import { shownError } from "@/lib/frappe";
import { Marquee } from "@/components/ui/marquee";

type AddState = { open: boolean; then: ((id: string) => void) | null; title: string };
export const useAddCompany = create<AddState>(() => ({ open: false, then: null, title: "" }));

/** 打开「添加公司」；then：建好（或点了已添加的）之后拿公司编号做什么；title：换个标题（默认「添加公司」）。 */
export function openAddCompany(then: (id: string) => void, title = "") {
  useAddCompany.setState({ open: true, then, title });
}

const close = () => useAddCompany.setState({ open: false, then: null, title: "" });

/** 候选一行的第二行：法定名 · 税号 · 分店 · 年度。 */
export function candidateLine(row: Pick<Candidate, "legal_name" | "tax_id" | "branch" | "years" | "title">): string {
  return [
    row.legal_name && row.legal_name !== row.title ? row.legal_name : "",
    row.tax_id ?? "",
    row.tax_id && row.branch ? branchShort(row.branch) : "",
    (row.years ?? []).join(" "),
  ].filter(Boolean).join(" · ");
}

export const AddCompanyDialog: FC = () => {
  const open = useAddCompany((s) => s.open);
  const title = useAddCompany((s) => s.title) || co.addCompany;
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(v) => !v && close()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-(--cc-z-sheet) bg-overlay data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          data-slot="cc-add-company"
          data-size="md"
          className="cc-card cc-enter cc-dialog cc-modal fixed left-1/2 z-(--cc-z-sheet) flex -translate-x-1/2 -translate-y-1/2 flex-col [--cc-modal-share:var(--cc-add-company-share)]"
        >
          <div className="cc-dialog-head">
            <DialogPrimitive.Title className="cc-greeting min-w-0 text-xl">
              <Marquee className="block truncate">{title}</Marquee>
            </DialogPrimitive.Title>
            <DialogPrimitive.Close className="cc-icon-btn" aria-label={t("close")}>
              <XIcon />
            </DialogPrimitive.Close>
          </div>
          <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
          {open && <Candidates />}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};

const Candidates: FC = () => {
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<Candidate[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const keys = useRef(new Map<string, string>()); // 同一行重点不重复建（同一个 request_key）
  const asked = useRef(0);

  const load = (text: string) => {
    const mine = ++asked.current;
    void listCandidates(text.trim()).then(
      (found) => mine === asked.current && (setRows(found), setError("")),
      (e: unknown) => mine === asked.current && (setRows((r) => r ?? []), setError(shownError(e))),
    );
  };
  useEffect(() => {
    const timer = window.setTimeout(() => load(search), search ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [search]);

  const finish = (id: string) => {
    const then = useAddCompany.getState().then;
    close();
    then?.(id);
  };
  const pick = async (row: Candidate) => {
    if (row.company) return finish(row.company);
    if (busy) return;
    setBusy(row.identity);
    setError("");
    try {
      const key = keys.current.get(row.identity) ?? requestKey();
      keys.current.set(row.identity, key);
      const made = await createCompany(row, key);
      finish(made.id);
    } catch (e) {
      setError(shownError(e));
    } finally {
      setBusy("");
    }
  };
  const refresh = async () => {
    setRefreshing(true);
    try {
      await refreshCatalogs();
      // 电脑读目录要几秒：隔一会儿再取两次候选
      for (const ms of [4000, 8000]) {
        await new Promise((r) => setTimeout(r, ms));
        load(search);
      }
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <>
      <div className="shrink-0 px-(--cc-dialog-px) pb-(--cc-space-8)">
        <label className="cc-search">
          <input
            autoFocus
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label={t("search")}
            placeholder={t("search")}
          />
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-(--cc-space-12) pb-(--cc-space-12)" data-slot="cc-candidates">
        {rows === null ? (
          <div className="flex justify-center py-(--cc-space-24) text-ink-3">
            <LoaderIcon className="cc-spinner" />
          </div>
        ) : rows.length ? (
          rows.map((row) => (
            <button
              key={row.identity}
              type="button"
              className="cc-list-row"
              disabled={!!busy && busy !== row.identity}
              onClick={() => void pick(row)}
            >
              <span className="min-w-0 flex-1">
                <Marquee className="block truncate text-ink">{row.title}</Marquee>
                <Marquee className="block truncate text-ink-3 text-xs">{candidateLine(row)}</Marquee>
              </span>
              {busy === row.identity ? (
                <LoaderIcon className="cc-spinner text-ink-3" />
              ) : row.company ? (
                <span className="shrink-0 text-ink-3 text-xs">{co.added}</span>
              ) : null}
            </button>
          ))
        ) : !search.trim() ? (
          <div className="flex justify-center py-(--cc-space-24)">
            <button type="button" className="cc-btn" disabled={refreshing} onClick={() => void refresh()}>
              {refreshing ? <LoaderIcon className="cc-spinner" /> : <RefreshCwIcon />}
              {co.refreshSets}
            </button>
          </div>
        ) : (
          <div className="px-2 py-6 text-center text-ink-3 text-sm">{t("noResults")}</div>
        )}
        {error && <p role="alert" className="px-(--cc-space-8) pt-(--cc-space-8) text-danger text-sm">{error}</p>}
      </div>
    </>
  );
};
