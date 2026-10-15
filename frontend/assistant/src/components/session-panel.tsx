// 右侧会话栏（设计文档 3.13 / 4.4；只在 Cowork 挂了公司的对话里，ERP / DMS 与没挂公司的对话没有它，ADR-020）。
//
//   版式   宽 280；卡片纵向、间距 10（components/company-cards.tsx 的 PanelCard）。宽屏与对话并排停靠；对话区放不下
//          「对话最窄 + 会话栏」时浮在对话上方（Esc、× 关）；手机盖满屏，从对话页胶囊菜单里的公司名打开，返回键关。
//          与文件预览共用右侧一个位置：打开文件时换成预览，关了预览回到会话栏（assistant.tsx 的 Layout）。
//   卡片   默认顺序 公司 / 本月 / 待处理 / 知识库 / 单据文件夹 / 定时任务 / 记住的选择 / 申报草稿 / 输出文件；没内容的不显示；
//          顺序、折叠记在这台设备（lib/company-panel.ts），卡头 6 点把手拖动或 Alt+↑/↓ 调顺序。
//   数据   公司详情按「公司|月份」缓存，只在会话栏挂着（看得见）时跟着站点事件重取（lib/company-panel 的 usePanelDetail）。
//
// 样式只用 globals.css 的 .cc-panel* 与变量。

import { useAuiState } from "@assistant-ui/react";
import { BookOpenIcon, Building2Icon, CalendarIcon, ChevronRightIcon, LoaderIcon, StoreIcon, XIcon } from "lucide-react";
import { Fragment, type FC, type PointerEvent as ReactPointerEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useOpenCompany } from "@/components/assistant-ui/projects";
import { companyLine } from "@/components/companies";
import {
  ChoicesSection,
  DraftsSection,
  FolderSection,
  NotesSection,
  PanelCard,
  PanelControlContext,
  type PanelControl,
  PendingCard,
  TasksSection,
} from "@/components/company-cards";
import { branchName } from "@/components/period-chip";
import { FileTypeIcon, useResults } from "@/components/results-panel";
import { co, t } from "@/i18n";
import { branchesOf, branchShort, type Detail, monthLabel, setsForScope } from "@/lib/companies";
import { moveCard, type PanelCard as CardId, setCardOrder, toggleCard, usePanel, usePanelDetail } from "@/lib/company-panel";
import { type Filing, NO_FILING, useProjects } from "@/lib/managed-agents/projects";
import { OPEN_LAYERS } from "@/lib/back-stack";

export type PanelMode = "docked" | "floating" | "phone";

/** 当前对话挂在哪家公司、哪个月、哪个分店（产品有公司分组时才有；ERP / DMS、没挂公司的对话 project 为空）。 */
export function useFiledCompany(): Filing {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const kind = useProjects((s) => s.kind);
  const filing = useProjects((s) => (remoteId && s.filings[remoteId]) || (localId && s.filings[localId]) || NO_FILING);
  return kind ? filing : NO_FILING;
}

export const SessionPanel: FC<{ filing: Filing; mode: PanelMode; onClose: () => void }> = ({ filing, mode, onClose }) => {
  const { project: company, period, scope } = filing;
  const { detail, error, reload } = usePanelDetail(company, period);
  const order = usePanel((s) => s.order);
  const collapsed = usePanel((s) => s.collapsed);
  const focus = usePanel((s) => s.focus);
  const box = useRef<HTMLElement>(null);
  const [dragging, setDragging] = useState<string | null>(null);

  // 看得见的卡片（空卡、本月卡不渲染）：调顺序只在它们之间挪
  const shownCards = () => [...(box.current?.querySelectorAll<HTMLElement>("[data-card]") ?? [])].map((el) => el.dataset.card ?? "");

  const control = useMemo<PanelControl>(() => {
    const drag = (id: string, e: ReactPointerEvent<HTMLElement>) => {
      if (e.button !== 0) return;
      e.preventDefault();
      setDragging(id);
      // 挂在窗口上：卡片换位置时 React 会挪动把手所在的节点，捕获在节点上会丢
      const onMove = (ev: PointerEvent) => {
        const cards = [...(box.current?.querySelectorAll<HTMLElement>("[data-card]") ?? [])];
        const ids = cards.map((el) => el.dataset.card ?? "");
        const from = ids.indexOf(id);
        // 目标位置 = 中线在指针上方的其他卡片有几张
        const to = cards.filter((el) => {
          if (el.dataset.card === id) return false;
          const rect = el.getBoundingClientRect();
          return rect.top + rect.height / 2 < ev.clientY;
        }).length;
        if (from >= 0 && to !== from) setCardOrder(moveCard(usePanel.getState().order, id as CardId, to - from, ids));
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        setDragging(null);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    };
    return {
      collapsed,
      dragging,
      toggle: (id) => toggleCard(id as CardId),
      move: (id, delta) => setCardOrder(moveCard(usePanel.getState().order, id as CardId, delta, shownCards())),
      drag,
    };
  }, [collapsed, dragging]);

  // 点了「公司名 ›」等：滚到那张卡（同一次只滚一回，之后的重取不再抢位置）
  const scrolled = useRef(0);
  useEffect(() => {
    if (!focus || scrolled.current === focus.at) return;
    const card = box.current?.querySelector<HTMLElement>(`[data-card="${focus.card}"]`);
    if (!card) return;
    scrolled.current = focus.at;
    card.scrollIntoView({ block: "nearest" });
  }, [focus, detail]);

  // 浮层、全屏：Esc 关（菜单、弹窗开着时它们先关）
  useEffect(() => {
    if (mode === "docked") return;
    const onKey = (e: KeyboardEvent) => {
      // 输入法选字时的 Esc 只是取消候选词
      if (e.key !== "Escape" || e.isComposing || e.defaultPrevented || document.querySelector(OPEN_LAYERS)) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mode, onClose]);

  const branches = detail ? branchesOf(detail) : [];
  const cards: Record<CardId, ReactNode> = {
    company: detail ? <CompanyCard detail={detail} period={period} scope={scope} /> : null,
    month: <MonthCard />,
    pending: <PendingCard company={company} />,
    notes: detail ? <NotesSection company={company} notes={detail.notes} reload={reload} /> : null,
    folders: detail ? <FolderSection company={company} folders={detail.folders} reload={reload} hideEmpty /> : null,
    tasks: detail ? <TasksSection company={company} detail={detail} period={period} /> : null,
    choices: detail ? <ChoicesSection company={company} choices={detail.choices} period={period} reload={reload} /> : null,
    drafts: detail?.drafts ? (
      <DraftsSection company={company} drafts={detail.drafts} branches={branches.length > 1 ? branches : []} combined={!!detail.company.combined_vat} />
    ) : null,
    outputs: <OutputsCard />,
  };
  return (
    <PanelControlContext.Provider value={control}>
      <aside ref={box} className="cc-panel" data-mode={mode} data-slot="cc-session-panel" aria-label={detail?.company.title || co.company}>
        {mode !== "docked" && (
          <div className="cc-panel-top">
            <span className="min-w-0 flex-1 truncate">{detail?.company.title ?? ""}</span>
            <button type="button" className="cc-icon-btn" aria-label={t("close")} title={t("close")} onClick={onClose}>
              <XIcon />
            </button>
          </div>
        )}
        {!detail && !error && (
          <div className="flex justify-center py-4 text-ink-3">
            <LoaderIcon className="size-4 animate-spin" />
          </div>
        )}
        {error && <p role="alert" className="px-2 text-danger text-sm">{error}</p>}
        {/* 键带公司：对话换到另一家时各卡重新挂上，新增 / 编辑框等不跟到另一家（同一家换对话时照常保留） */}
        {order.map((id) => (
          <Fragment key={`${company}:${id}`}>{cards[id]}</Fragment>
        ))}
      </aside>
    </PanelControlContext.Provider>
  );
};

// ---------------------------------------------------------------- 公司
const InfoRow: FC<{ icon: ReactNode; label: string; value: string }> = ({ icon, label, value }) => (
  <div className="cc-panel-row" title={value}>
    {icon}
    <span className="min-w-0 flex-1 truncate">{label}</span>
    <span className="cc-panel-extra">{value}</span>
  </div>
);

/** 公司卡：名称（点了打开公司页）、法定名、税号 · 分店，合并申报；本对话的月份、分店与覆盖这个月的账套
 *  （没指定分店时各分店的都列，总店在前，一份一行；lib/companies 的 setsForScope）。 */
const CompanyCard: FC<{ detail: Detail; period: string; scope: string }> = ({ detail, period, scope }) => {
  const openCompany = useOpenCompany();
  const c = detail.company;
  const branch = branchesOf(detail).find((b) => b.branch === scope);
  const sets = setsForScope(detail, period, scope);
  const legal = c.legal_name && c.legal_name !== c.title ? c.legal_name : "";
  const line = companyLine(c);
  return (
    <PanelCard id="company" title={co.company} slot="cc-panel-company">
      <button type="button" className="cc-panel-row" data-strong="" title={co.openCompany} onClick={() => openCompany(c.id)}>
        <Building2Icon />
        <span className="min-w-0 flex-1 truncate">{c.title}</span>
        <ChevronRightIcon />
      </button>
      {legal && <div className="cc-panel-row cc-panel-indent"><span className="truncate">{legal}</span></div>}
      {line && <div className="cc-panel-row cc-panel-indent"><span className="truncate">{line}</span></div>}
      {!!c.combined_vat && (
        <div className="cc-panel-row cc-panel-indent">
          <span className="cc-chip">{co.combinedVat}</span>
        </div>
      )}
      {period && <InfoRow icon={<CalendarIcon />} label={co.period} value={monthLabel(period)} />}
      {scope && <InfoRow icon={<StoreIcon />} label={co.branch} value={branch ? branchName(branch) : branchShort(scope)} />}
      {sets.length === 1 && <InfoRow icon={<BookOpenIcon />} label={co.accountSets} value={sets[0]!.label} />}
      {sets.length > 1 && (
        <>
          <InfoRow icon={<BookOpenIcon />} label={co.accountSets} value="" />
          {sets.map((set) => (
            <div key={`${set.connection}|${set.key}`} className="cc-panel-row cc-panel-indent" title={set.label}>
              <span className="min-w-0 flex-1 truncate">{set.label}</span>
            </div>
          ))}
        </>
      )}
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 本月
/** 「本月」：第 1 期月结台账与统一异常清单落在这里（设计文档 7 第 4 段）；台账做好之前不显示。 */
const MonthCard: FC = () => null;

// ---------------------------------------------------------------- 输出文件
/** 本对话产出的文件：点一行在右侧预览（换成文件预览，关了回到会话栏）。 */
const OutputsCard: FC = () => {
  const { files, openFile } = useResults();
  if (!files.length) return null;
  return (
    <PanelCard id="outputs" title={t("files")} summary={files.length} slot="cc-panel-outputs">
      {files.map((file) => (
        <button key={file.id} type="button" className="cc-panel-row" title={file.filename} onClick={() => openFile(file.id)}>
          <FileTypeIcon filename={file.filename} />
          <span className="min-w-0 flex-1 truncate">{file.filename}</span>
        </button>
      ))}
    </PanelCard>
  );
};
