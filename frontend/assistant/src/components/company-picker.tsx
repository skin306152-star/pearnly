// 选择公司（用户 11-07：对话里点「公司」不再是下拉，弹出这个窗口；左侧「公司」与公司页删掉）：原生对话框，搜索 + MR.ERP / Express
// 标签 + 连接里读到的全部账套公司。
//
//   列表来自服务端 choices（产品声明的公司接口：Cowork companies.api.choices、ERP masters.cc_companies.choices）：已建的公司与账套
//   目录里还没建的按身份合成一张表，搜索、按标签筛、分页（每页 50，「显示更多」）都在服务端；我有对话的按最近活动在前，其余按名称。
//   没建过的那行点了用现有 create 顺手建（幂等 request_key，同一行重点不重建），建好就在这段对话里用它，不再有单独的「添加公司」一步。
//   没建的行只给能建的人（服务端判：Cowork 要录入权限，ERP 要开单权限且没被限定公司）。
//   标签按服务端给的 adapters 出（不按标签筛时有哪几种），有一种就摆，最后一个总是「云端 Express」（云端那份 CC 报的账套，服务端按
//   报它的电脑归到 express_cloud，第二阶段评估 4.8；本地的叫「本地 Express」）；ERP 有不接账本的纯 ERP 公司时多一个「ERP」。没点过标签时
//   停在第一行那种（我最近用的那家的 ERP）；标签只筛这次打开里看的，不记住。
//   第一行「最近」= 不挂公司（新对话里才有：current 给了字符串）；当前选中的那行打勾。
//   头部「刷新账套」（产品声明了 catalog 才有）：本人能用的每条连接各读一次目录，隔几秒再取两次列表。
//   一家都没有、也没在搜：「没有结果」+「连接」（打开设置 → 连接；设置里没有「连接」一栏的地方不出这个按钮），不加说明文字。
//
// 打开方式：openCompanyPicker(then, current?)；then 收到公司编号与显示名（""= 选了「最近」）。current：新对话下巴、手机问候行、
// 代录新建页传草稿的公司（""= 最近）；定时任务「＋」先选公司时不传（必须选一家，不出「最近」）。对话框在 assistant.tsx 里挂一次。

import { CheckIcon, LoaderIcon, PlugIcon, RefreshCwIcon, XIcon } from "lucide-react";
import { Dialog, DialogClose, DialogDescription, DialogHeading, DialogOverlay, DialogPortal, DialogSurface } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { type FC, useEffect, useRef, useState } from "react";
import { co, st, t } from "@/i18n";
import { create } from "zustand";
import {
  branchShort,
  type Candidate,
  CHOICES_PAGE,
  type CompanyChoice,
  createCompany,
  listChoices,
  refreshCatalogs,
  requestKey,
} from "@/lib/companies";
import { openSettings, useHasConnections } from "@/lib/desktop";
import { useSection } from "@/lib/managed-agents/projects";
import { shownError } from "@/lib/frappe";
import { Marquee } from "@/components/ui/marquee";
import { reportCaught } from "../lib/caught";

type PickerState = { open: boolean; then: ((id: string, title: string) => void) | null; current: string | null };
export const useCompanyPicker = create<PickerState>(() => ({ open: false, then: null, current: null }));

/** 打开「选择公司」；then：选好（没建的先建好）之后拿公司编号与显示名（""= 最近）；current：当前草稿的公司（给了才有「最近」一行）。 */
// map: company-picker
export function openCompanyPicker(then: (id: string, title: string) => void, current: string | null = null) {
  useCompanyPicker.setState({ open: true, then, current });
}

const close = () => useCompanyPicker.setState({ open: false, then: null, current: null });

// 标签顺序同定时任务表单的目标卡片；产品名不翻译
const CLOUD = "express_cloud";
const ERPS = [
  { adapter: "mrerp", label: "MR.ERP" },
  { adapter: "express", label: co.localExpress },
  { adapter: "erp", label: "ERP" },
  { adapter: CLOUD, label: co.cloudExpress },
];

/** 摆哪几个标签（服务端 adapters 里认得的，按 ERPS 的先后，最后是占位的「云端 Express」）；一种都没有时不摆。
 *  「云端 Express」标签只在服务端说显示时才摆（`window.pearnly_cloud_express`，正式部署里先不显示，见 `cc_desktop/cloud_entry.py`）。 */
export function tabsOf(adapters: readonly string[], cloud = window.pearnly_cloud_express === true): string[] {
  const kinds = ERPS.map((e) => e.adapter).filter((a) => a !== CLOUD && adapters.includes(a));
  return kinds.length ? (cloud ? [...kinds, CLOUD] : kinds) : [];
}

/** 没点过标签时停在哪个：第一行那种（我最近用的那家的 ERP），第一行认不出就第一个标签；不摆标签时 ""（不按标签筛）。 */
export function firstTab(adapters: readonly string[], rows: ReadonlyArray<Pick<Candidate, "adapter">>): string {
  const kinds = tabsOf(adapters);
  const first = rows[0]?.adapter ?? "";
  return kinds.includes(first) ? first : kinds[0] ?? "";
}

/** 一行的第二行：法定名 · 税号 · 分店 · 年度。 */
export function candidateLine(row: Pick<Candidate, "legal_name" | "tax_id" | "branch" | "years" | "title">): string {
  return [
    row.legal_name && row.legal_name !== row.title ? row.legal_name : "",
    row.tax_id ?? "",
    row.tax_id && row.branch ? branchShort(row.branch) : "",
    (row.years ?? []).join(" "),
  ].filter(Boolean).join(" · ");
}

export const CompanyPickerDialog: FC = () => {
  const open = useCompanyPicker((s) => s.open);
  const canRefresh = useSection("catalog");
  const [refreshing, setRefreshing] = useState(false);
  const reload = useRef<() => void>(() => undefined);
  const refresh = async () => {
    setRefreshing(true);
    try {
      await refreshCatalogs();
      // 电脑读目录要几秒：隔一会儿再取两次列表
      for (const ms of [4000, 8000]) {
        await new Promise((r) => setTimeout(r, ms));
        reload.current();
      }
    } finally {
      setRefreshing(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(v) => !v && close()}>
      <DialogPortal>
        <DialogOverlay />
        <DialogSurface
          data-slot="cc-company-picker"
          data-size="md"
          className="cc-dialog cc-enter cc-modal fixed left-1/2 z-(--cc-z-sheet) flex -translate-x-1/2 -translate-y-1/2 flex-col [--cc-modal-share:var(--cc-company-picker-share)]"
        >
          <div className="cc-dialog-head">
            <DialogHeading className="cc-dialog-title">
              <Marquee className="block truncate">{co.chooseCompany}</Marquee>
            </DialogHeading>
            <span className="flex shrink-0 items-center gap-(--cc-space-4)">
              {canRefresh && (
                <button
                  type="button"
                  className="cc-icon-btn"
                  data-slot="cc-company-picker-refresh"
                  aria-label={co.refreshSets}
                  title={co.refreshSets}
                  disabled={refreshing}
                  onClick={() => void refresh()}
                >
                  {refreshing ? <LoaderIcon className="cc-spinner" /> : <RefreshCwIcon />}
                </button>
              )}
              <DialogClose className="cc-icon-btn" aria-label={t("close")}>
                <XIcon />
              </DialogClose>
            </span>
          </div>
          <DialogDescription className="sr-only">{co.chooseCompany}</DialogDescription>
          {open && <Choices reload={reload} />}
        </DialogSurface>
      </DialogPortal>
    </Dialog>
  );
};

const Choices: FC<{ reload: { current: () => void } }> = ({ reload }) => {
  const current = useCompanyPicker((s) => s.current);
  const [search, setSearch] = useState("");
  const [erp, setErp] = useState(""); // ""= 还没定（第一次取回来两种以上时停在第一行那种）
  const [adapters, setAdapters] = useState<string[]>([]);
  const [rows, setRows] = useState<CompanyChoice[] | null>(null);
  const [more, setMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const hasConnections = useHasConnections();
  const keys = useRef(new Map<string, string>()); // 同一行重点不重复建（同一个 request_key）
  const asked = useRef(0);

  const load = (text: string, tab: string, start = 0) => {
    const mine = ++asked.current;
    if (start) setLoadingMore(true);
    void listChoices(text.trim(), tab, start)
      .then(
        (out) => {
          if (mine !== asked.current) return;
          const opening = tab ? "" : firstTab(out.adapters, out.rows);
          if (opening) return setErp(opening); // 两种以上：停在第一行那种，按它重取（服务端按标签筛、分页）
          setAdapters(tabsOf(out.adapters));
          setRows((now) => (start ? [...(now ?? []), ...out.rows] : out.rows));
          setMore(out.rows.length >= CHOICES_PAGE);
          setError("");
        },
        (e: unknown) => {
          if (mine !== asked.current) return;
          setRows((now) => now ?? []);
          setMore(false);
          setError(shownError(e));
        },
      )
      .finally(() => mine === asked.current && setLoadingMore(false));
  };
  reload.current = () => load(search, erp);
  useEffect(() => {
    const timer = window.setTimeout(() => load(search, erp), search ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [search, erp]); // eslint-disable-line react-hooks/exhaustive-deps -- 搜索词或标签变了才重取

  const finish = (id: string, title: string) => {
    const then = useCompanyPicker.getState().then;
    close();
    then?.(id, title);
  };
  const pick = async (row: CompanyChoice) => {
    if (row.company) return finish(row.company, row.title);
    if (busy) return;
    setBusy(row.identity);
    setError("");
    try {
      const key = keys.current.get(row.identity) ?? requestKey();
      keys.current.set(row.identity, key);
      const made = await createCompany(row, key);
      finish(made.id, made.title || row.title);
    } catch (e) { reportCaught(e, "company-picker");
      setError(shownError(e));
    } finally {
      setBusy("");
    }
  };
  const connect = () => {
    close();
    openSettings("connections");
  };

  const tabs = ERPS.filter((e) => adapters.includes(e.adapter)); // adapters 已是 tabsOf：一种都没有时为空
  const typing = !!search.trim();
  const recents = current !== null && !typing;
  const nothing = rows !== null && !rows.length && !typing; // 一家都没有（没在搜）

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
      {tabs.length > 0 && (
        <Tabs
          value={erp}
          onValueChange={(value) => {
            setRows(null);
            setErp(value);
          }}
          className="shrink-0"
          data-slot="cc-company-picker-tabs"
        >
          <TabsList variant="line" className="w-full justify-start" aria-label={co.companies}>
            {tabs.map((e) => (
              <TabsTrigger key={e.adapter} value={e.adapter} className="flex-none">
                {e.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-(--cc-space-12) pb-(--cc-space-12)" data-slot="cc-candidates">
        {recents && (
          <button type="button" className="cc-list-row" data-slot="cc-company-picker-recents" disabled={!!busy || rows === null} onClick={() => finish("", "")}>
            <Marquee className="min-w-0 flex-1 truncate text-ink">{t("recents")}</Marquee>
            {current === "" && <CheckIcon className="shrink-0 text-ink-3" />}
          </button>
        )}
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
              data-built={row.company ? "" : undefined}
              data-chosen={row.company && row.company === current ? "" : undefined}
              disabled={!!busy && busy !== row.identity}
              onClick={() => void pick(row)}
            >
              <span className="min-w-0 flex-1">
                <Marquee className="block truncate text-ink">{row.title}</Marquee>
                <Marquee className="block truncate text-ink-3 text-caption">{candidateLine(row)}</Marquee>
              </span>
              {busy === row.identity ? (
                <LoaderIcon className="cc-spinner text-ink-3" />
              ) : row.company && row.company === current ? (
                <CheckIcon className="shrink-0 text-ink-3" />
              ) : null}
            </button>
          ))
        ) : (
          <div className="cc-empty-line px-(--cc-space-8)" data-slot="cc-company-picker-empty">
            {t("noResults")}
          </div>
        )}
        {nothing && hasConnections && (
          <div className="flex justify-center pb-(--cc-space-24)">
            <button type="button" className="cc-btn" data-slot="cc-company-picker-connect" onClick={connect}>
              <PlugIcon />
              {st.connect}
            </button>
          </div>
        )}
        {more && rows && (
          <button type="button" className="cc-show-more" disabled={loadingMore} onClick={() => load(search, erp, rows.length)}>
            {co.showMore}
          </button>
        )}
        {error && <p role="alert" className="px-(--cc-space-8) pt-(--cc-space-8) text-danger text-body">{error}</p>}
      </div>
    </>
  );
};
