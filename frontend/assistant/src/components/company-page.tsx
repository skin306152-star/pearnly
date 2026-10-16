// 公司页（?company=<编号>；公司列表、侧栏组「···」「打开公司」、会话栏公司卡点进来）。一家客户一份，全所共用（Cowork companies 域）。
// 设计文档 4.5（用户 10-16 定「中间应该是对话框，信息放右侧」，同 claude.ai 的项目页）：版式同对话页——
//
//   中间一栏（宽 768，同对话）  标题 = 公司名（点了就地改名，手机用输入框）；「···」：改名、刷新账套、删除公司（不确认）
//                              → 输入框（同新对话的输入框 + 下巴：公司固定，月份胶囊最近 12 个月 + 不指定、默认上个月，
//                                多分店时分店胶囊；发出 = 在这家开对话并发出）
//                              → 这家的对话（只列我的，从服务端按这家取，分组列表 group=<公司>，分页「显示更多」）：行同侧栏行，
//                                标题、第二行「月份 · 分店」、右侧相对时间，最新的在前；行尾装饰只用真实信号（lib/company-panel 的 chatState）
//   右侧会话栏                 公司页默认开着（开关、卡片顺序与折叠同对话共用）：公司（信息 + 账套）、月结（月份胶囊 = 本页的月份）、
//                              待处理（全部月份）、知识库（空时「让 CC 起草」）、单据文件夹、定时任务、记住的选择、申报草稿
//                              （components/session-panel.tsx 的 page）。不再有「对话 / 资料 / 定时任务」标签。
//   手机                       单栏；标题旁的按钮打开盖满屏的会话栏。
//
// 别处改了这家（pearnly.project upsert）重取，删了（delete）回到公司列表。文案走 i18n.ts 的 co；样式只用 globals.css 的 .cc-* 与变量。

import { useAui } from "@assistant-ui/react";
import { ArrowUpIcon, Building2Icon, LoaderIcon, MoreHorizontalIcon, PanelRightIcon, PencilIcon, PlusIcon, RefreshCwIcon, TrashIcon } from "lucide-react";
import { DropdownMenu } from "@/components/ui/menu";
import { type FC, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useStartIn } from "@/components/assistant-ui/projects";
import { BranchChip, PeriodChip } from "@/components/period-chip";
import { useSidebar } from "@/components/ui/sidebar";
import { co, t } from "@/i18n";
import {
  type Branch,
  branchesOf,
  branchShort,
  deleteCompany,
  type Detail,
  dropCachedDetail,
  forgetAnyCompany,
  monthShort,
  refreshCatalogs,
  renameCompany,
  useCompanyNav,
} from "@/lib/companies";
import {
  chatMark,
  chatState,
  forgetPanelDetail,
  newestFirst,
  relativeWhen,
  setPagePeriod,
  setPanelOverlay,
  unreadRuns,
  useMinuteClock,
  usePageMonth,
  usePanelDetail,
} from "@/lib/company-panel";
import { noteRun, runningSessions, useActivities } from "@/lib/managed-agents/activity";
import { type ListedSession, listGrouped } from "@/lib/managed-agents/api";
import type { RunStatus } from "@/lib/managed-agents/events";
import { forgetProject, renameProjectLocally, useProjects } from "@/lib/managed-agents/projects";
import { onProjectChange } from "@/lib/managed-agents/realtime";
import { useView } from "@/lib/nav";
import { shownError } from "@/lib/frappe";
import { isImeEnter } from "@/lib/ime";
import { ACCEPT } from "@/lib/managed-agents/attachments";

// 卡片与各节的内容搬到了 company-cards.tsx（会话栏用）；原来从本文件导入的照样能用
export {
  ChoiceRow,
  choiceSection,
  commitNote,
  FolderSection,
  folderState,
  NoteEditor,
  noteKey,
  noteText,
  putNote,
  removeNote,
  setChoiceShown,
  setFor,
  SetsSection,
} from "@/components/company-cards";
import { Marquee } from "@/components/ui/marquee";

// ---------------------------------------------------------------- 页面
export const CompanyPage: FC = () => {
  const company = useCompanyNav((s) => s.company);
  const { setView } = useView();
  // 本页的月份：输入框的月份胶囊与会话栏「月结」卡的月份胶囊同一个
  const period = usePageMonth();
  // 与会话栏共用同一份详情（lib/company-panel 的 usePanelDetail：只取一次、只一处跟着实时事件重取）。推来的主档变化之外，
  // 再按站点通用的做法核对：电脑同步文件夹、回到前台、窗口拿回焦点、实时通道重连时重取，另有 60 秒兜底
  // （2026-10-15 验收 F8：另一台设备「更换…」文件夹后，这页错过了那次推送就一直停在旧路径）
  const { detail, error, reload } = usePanelDetail(company, period);
  // 别处删了这家：回到公司列表
  useEffect(
    () =>
      onProjectChange((change) => {
        if (change.op === "delete" && change.id === company) setView("companies");
      }),
    [company, setView],
  );

  if (!company) return null;
  if (!detail) {
    return (
      <div className="flex flex-1 items-center justify-center text-ink-3">
        {error ? <p role="alert" className="text-danger text-sm">{error}</p> : <LoaderIcon className="size-4 animate-spin" />}
      </div>
    );
  }
  // 键带公司：换到另一家（有缓存时不经过加载态）时整页重新挂上，输入框的字与分店不串到另一家
  return <Master key={detail.company.id} detail={detail} period={period} reload={reload} error={error} />;
};

const Master: FC<{ detail: Detail; period: string; reload: () => Promise<void>; error: string }> = ({ detail, period, reload, error }) => {
  const id = detail.company.id;
  return (
    <div data-slot="cc-scroll" className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="cc-company-page" data-slot="cc-company-page">
        <Header detail={detail} reload={reload} />
        {error && <div role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-danger text-sm">{error}</div>}
        <CompanyComposer company={id} title={detail.company.title} period={period} branches={branchesOf(detail)} />
        <Conversations company={id} />
      </div>
    </div>
  );
};

// ---------------------------------------------------------------- 顶部
const Header: FC<{ detail: Detail; reload: () => Promise<void> }> = ({ detail, reload }) => {
  const { isMobile } = useSidebar();
  const { setView } = useView();
  const c = detail.company;
  // 改名成功就显示新名字（服务端回的那份），不等整页重取；重取到了以服务端为准
  const [title, setTitle] = useState(c.title);
  useEffect(() => setTitle(c.title), [c.title]);
  const [renaming, setRenaming] = useState(false);
  const [value, setValue] = useState(c.title);
  const [error, setError] = useState("");
  // 从「···」菜单点了重命名：等菜单关完再出改名框（第 5 轮测试 3，两轮实测焦点都落在 body）。菜单开着时有焦点陷阱：
  // 在选中那一下就挂上改名框，它自动聚焦会被陷阱拉回菜单项，菜单项随即卸下，焦点掉到 body。组件库在陷阱拆掉之后
  // （onCloseAutoFocus，卸下后的下一轮）才问焦点该去哪：在那里不还给「···」、改为出改名框，它挂上时已没有陷阱。
  const renameFromMenu = useRef(false);
  const save = async (input: string) => {
    const next = input.trim();
    setRenaming(false);
    if (!next || next === title) return;
    setError("");
    try {
      const out = await renameCompany(c.id, next);
      const saved = (out as { title?: string } | null)?.title || next;
      setTitle(saved);
      renameProjectLocally(c.id, saved);
      dropCachedDetail(c.id);
    } catch (e) {
      setError(shownError(e));
    }
  };
  const startRename = () => {
    setValue(title);
    setRenaming(true);
  };
  const rename = () => {
    if (!isMobile) return startRename();
    const next = window.prompt(t("rename"), title);
    if (next) void save(next);
  };
  // 刷新账套：本人能用的每条连接各请电脑读一次目录（同添加公司里的「刷新账套」），再重取这家
  const refreshSets = async () => {
    setError("");
    try {
      await refreshCatalogs();
      dropCachedDetail(c.id);
      await reload();
    } catch (e) {
      setError(shownError(e));
    }
  };
  const remove = async () => {
    try {
      await deleteCompany(c.id);
      forgetProject(c.id);
      forgetAnyCompany();
      dropCachedDetail(c.id);
      forgetPanelDetail(c.id);
      setView("companies");
    } catch (e) {
      setError(shownError(e));
    }
  };
  return (
    <div className="cc-page-head items-start">
      <div className="min-w-0 flex-1">
        {renaming ? (
          <TitleRename value={value} onChange={setValue} onDone={(saveIt) => (saveIt ? void save(value) : setRenaming(false))} />
        ) : (
          // 按钮里面放滚动的文字（不是按钮自己滚）：滚动时的渐隐遮罩只盖文字，不把按钮的焦点环裁掉
          <h1 className="cc-page-title" data-editable="">
            <button type="button" className="cc-page-title-btn" title={t("rename")} onClick={rename}>
              <Marquee className="cc-page-title-text">{title}</Marquee>
            </button>
          </h1>
        )}
        {error && <p role="alert" className="mt-1 text-danger text-sm">{error}</p>}
      </div>
      {/* 手机：会话栏盖满屏，从这里打开（电脑上用标题栏右上的开关） */}
      {isMobile && (
        <button type="button" className="cc-icon-btn" aria-label={t("showPanel")} title={t("showPanel")} onClick={() => setPanelOverlay(true)}>
          <PanelRightIcon />
        </button>
      )}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger className="cc-icon-btn" aria-label={t("more")}>
          <MoreHorizontalIcon />
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="end"
            sideOffset={4}
            className="cc-menu !min-w-44"
            onCloseAutoFocus={(e) => {
              if (!renameFromMenu.current) return;
              renameFromMenu.current = false;
              e.preventDefault(); // 不还给「···」：改名框此刻才挂上，自己拿到焦点
              startRename();
            }}
          >
            <DropdownMenu.Item
              className="cc-menu-item"
              onSelect={() => {
                // 电脑：菜单关完再出改名框（见 renameFromMenu）；手机：系统输入框，照旧当场弹
                if (isMobile) return rename();
                renameFromMenu.current = true;
              }}
            >
              <PencilIcon />
              {t("rename")}
            </DropdownMenu.Item>
            <DropdownMenu.Item className="cc-menu-item" onSelect={() => void refreshSets()}>
              <RefreshCwIcon />
              {co.refreshSets}
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="cc-menu-sep" />
            <DropdownMenu.Item className="cc-menu-item" data-danger="" onSelect={() => void remove()}>
              <TrashIcon />
              {co.deleteCompany}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
};

/** 公司名就地改名（同对话标题的 ThreadListItemRename，Q 线）：与标题同字号字重、一行高、透明底、聚焦 1.5px 蓝描边；
 *  回车保存（输入法选字的回车不算）、Esc 放弃（选字时的 Esc 只取消候选词），只结束一次（Esc 卸下输入框时的失焦不再存一遍）。 */
const TitleRename: FC<{ value: string; onChange: (value: string) => void; onDone: (save: boolean) => void }> = ({ value, onChange, onDone }) => {
  const finished = useRef(false);
  const box = useRef<HTMLInputElement>(null);
  // 挂上就把光标放进来、全选（不只靠 autoFocus：挂上那一刻别的东西抢过焦点时这里再拿一次）
  useLayoutEffect(() => {
    const el = box.current;
    if (el && document.activeElement !== el) el.focus({ preventScroll: true });
    el?.select();
  }, []);
  const finish = (saveIt: boolean) => {
    if (finished.current) return;
    finished.current = true;
    onDone(saveIt);
  };
  return (
    <input
      ref={box}
      autoFocus
      value={value}
      aria-label={t("rename")}
      onChange={(e) => onChange(e.target.value)}
      onFocus={(e) => e.target.select()}
      onBlur={() => finish(true)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !isImeEnter(e)) {
          e.preventDefault();
          finish(true);
        }
        if (e.key === "Escape" && !e.nativeEvent.isComposing) {
          e.preventDefault();
          finish(false);
        }
      }}
      className="cc-inline-edit cc-page-title-edit"
    />
  );
};

// ---------------------------------------------------------------- 输入框
/** 同新对话的输入框与下巴（设计文档 3.12）：下巴里公司固定（不可换），月份、分店胶囊；发出 = 在这家开对话并发出。
 *  随内容长高（最高同新对话）；回车发送，输入法选字的回车不算（Safari 的 keyCode 229，lib/ime.ts）；「＋」选文件 = 在这家开
 *  新对话，打了的字与选的文件放进那边的输入框（上传进度、出错、发出都在那边，同拖文件进输入框）。 */
const CompanyComposer: FC<{ company: string; title: string; period: string; branches: Branch[] }> = ({ company, title, period, branches }) => {
  const start = useStartIn();
  const withPeriod = useProjects((s) => s.period);
  const withScope = useProjects((s) => s.scope);
  const [scope, setScope] = useState("");
  const [text, setText] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const filing = () => ({ project: company, period: withPeriod ? period : "", scope: withScope ? scope : "" });
  const send = () => {
    const value = text.trim();
    if (!value) return;
    setText("");
    start(filing(), value, true);
  };
  const attach = (files: FileList | null) => {
    const picked = files ? Array.from(files) : [];
    if (picker.current) picker.current.value = "";
    if (!picked.length) return;
    const value = text.trim();
    setText("");
    start(filing(), value, false, picked);
  };
  // 随内容长高：先收回再按内容量（上限由 max-h-72 管，超出在框里滚动）
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);
  return (
    <div className="flex w-full flex-col" data-slot="cc-company-composer">
      <div className="cc-composer cc-composer-inline">
        <textarea
          ref={box}
          rows={1}
          value={text}
          aria-label={t("placeholder")}
          placeholder={t("placeholder")}
          enterKeyHint="send"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !isImeEnter(e)) {
              e.preventDefault();
              send();
            }
          }}
          className="block max-h-72 min-h-6 w-full resize-none overflow-y-auto bg-transparent font-sans outline-none"
        />
        <div className="aui-composer-action-wrapper relative flex items-center justify-between">
          <button type="button" className="aui-composer-add-attachment cc-attach" aria-label={t("attach")} title={t("attach")} onClick={() => picker.current?.click()}>
            <PlusIcon className="stroke-[1.5px]" />
          </button>
          <input ref={picker} type="file" multiple accept={ACCEPT} hidden onChange={(e) => attach(e.target.files)} />
          <div className="aui-composer-send-slot ms-auto flex items-center gap-1.5">
            <button type="button" className="cc-send" aria-label={t("send")} title={t("send")} disabled={!text.trim()} onClick={send}>
              <ArrowUpIcon />
            </button>
          </div>
        </div>
      </div>
      <div className="cc-composer-chin" data-slot="cc-composer-chin">
        <span className="cc-chin-btn cc-chin-fixed" data-slot="cc-chin-company" data-chosen="">
          <Building2Icon />
          <Marquee className="min-w-0 truncate">{title}</Marquee>
        </span>
        {withPeriod && <PeriodChip look="cc-chin-btn" side="top" value={period} onChange={setPagePeriod} />}
        {withScope && <BranchChip look="cc-chin-btn" side="top" branches={branches} value={scope} onChange={setScope} />}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------- 对话
const MARK_LABEL: Record<"running" | "notice" | "unread", () => string> = {
  running: () => t("running"),
  notice: () => t("newNotice"),
  unread: () => t("unread"),
};
// 页面开着时开始跑的对话：等这么久再补读运行状态（几段一起开始跑时合成一次请求）
const RUN_RECHECK_MS = 1000;
// 侧栏条数变化与移动推送常常前后脚到：这么久内的合成一次重取
const RELOAD_MERGE_MS = 150;

/** 我在这家的对话：按这家从服务端取（分组列表 group=<公司>，同侧栏「显示更多」的翻页），最新的在前；一行同侧栏行：标题、
 *  第二行「月份 · 分店」、右侧相对时间，行尾装饰（在跑脉冲点 / 等你处理 / 跑完没看）；点了切过去。顺带请服务端核对侧栏以为在跑的那几个（check，最多 10 个，同侧栏的做法），
 *  读回的运行状态交给 noteRun 校正实时状态（同侧栏 session-list-adapter 的 absorb），装饰只看实时状态；读回里有等你点
 *  「允许」的、且还在跑的算「等你处理」。页面开着时才开始跑的对话补读一次它的运行状态；不在跑了丢掉读回的，下次开始跑再读。
 *  这家的对话有进出（新建、移入移出、侧栏挂上的条数变了）或公司有变化时重取第一页。 */
export const Conversations: FC<{ company: string }> = ({ company }) => {
  const aui = useAui();
  const now = useMinuteClock(); // 相对时间每分钟跟着走（「刚刚」不会一直停着）
  const { setView } = useView();
  const [rows, setRows] = useState<ListedSession[] | null>(null);
  const [runs, setRuns] = useState<Record<string, RunStatus>>({});
  const [next, setNext] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const asked = useRef(0);
  // 已请服务端读过运行状态的对话（读了没带回的不反复读）
  const checked = useRef(new Set<string>());
  const absorbRuns = useCallback((ids: readonly string[], found: Record<string, RunStatus> | undefined) => {
    ids.forEach((id) => checked.current.add(id));
    for (const [id, run] of Object.entries(found ?? {})) noteRun(id, run);
    setRuns((before) => ({ ...before, ...(found ?? {}) }));
  }, []);
  const load = useCallback(
    async (after = "") => {
      const mine = ++asked.current;
      setBusy(true);
      try {
        const check = runningSessions();
        const page = await listGrouped({ group: company, after, check });
        if (mine !== asked.current) return;
        // 标题空 = 还没发过消息（首条消息一发就有临时标题）：输入框加附件要先建会话，选了文件不发也会在服务端留一条
        // （第 5 轮测试 4）。公司页只列发过的；这个列表自己取数，不经侧栏的多设备同步，滤掉不会被当成别处删了
        setRows((before) => {
          const kept = after ? (before ?? []) : [];
          const seen = new Set(kept.map((row) => row.id));
          return [...kept, ...page.sessions.filter((row) => !seen.has(row.id) && !row.archived_at && row.title)];
        });
        absorbRuns(check, page.runs);
        setNext(page.nextCursor);
      } catch {
        if (mine === asked.current) setRows((before) => before ?? []);
      } finally {
        if (mine === asked.current) setBusy(false);
      }
    },
    [company, absorbRuns],
  );
  useEffect(() => void load(), [load]);
  // 有进出时重取第一页：只看移进这家、或从这一页里移走的（别家的移动不管）；侧栏条数变化与推送常常一起来，合成一次
  const rowsNow = useRef(rows);
  rowsNow.current = rows;
  const soon = useRef(0);
  useEffect(() => {
    const reload = () => {
      window.clearTimeout(soon.current);
      soon.current = window.setTimeout(() => void load(), RELOAD_MERGE_MS);
    };
    // 侧栏知道的、挂在这家的对话条数（新建 / 移入移出时会变）：变了就重取
    const filedHere = () => Object.values(useProjects.getState().filings).filter((f) => f.project === company).length;
    let filed = filedHere();
    const offFiled = useProjects.subscribe(() => {
      const count = filedHere();
      if (count === filed) return;
      filed = count;
      reload();
    });
    const offMove = onProjectChange((change) => {
      if (change.op !== "move") return;
      if (change.project === company || rowsNow.current?.some((row) => row.id === change.session_id)) reload();
    });
    return () => {
      offFiled();
      offMove();
      window.clearTimeout(soon.current);
    };
  }, [company, load]);
  const activities = useActivities((rows ?? []).map((row) => row.id));
  const running = (rows ?? []).filter((row) => activities[row.id] === "running").map((row) => row.id);
  const runningKey = running.join(",");
  const missing = unreadRuns(running, runs, checked.current).join(",");
  // 不在跑了：丢掉读回的运行状态，下次开始跑重新读
  useEffect(() => {
    const now = new Set(runningKey ? runningKey.split(",") : []);
    for (const id of [...checked.current]) if (!now.has(id)) checked.current.delete(id);
    setRuns((before) => {
      const kept = Object.fromEntries(Object.entries(before).filter(([id]) => now.has(id)));
      return Object.keys(kept).length === Object.keys(before).length ? before : kept;
    });
  }, [runningKey]);
  // 页面开着时才开始跑的：稍等合并后补读一次（只读运行状态，不换列表，翻过的页不丢）
  useEffect(() => {
    if (!missing) return;
    const ids = missing.split(",");
    const timer = setTimeout(() => {
      listGrouped({ group: company, check: ids })
        .then((page) => absorbRuns(ids, page.runs))
        .catch(() => ids.forEach((id) => checked.current.add(id)));
    }, RUN_RECHECK_MS);
    return () => clearTimeout(timer);
  }, [missing, company, absorbRuns]);
  if (!rows) return <LoaderIcon className="size-4 animate-spin text-ink-3" aria-hidden />;
  return (
    <section className="flex flex-col" data-slot="cc-company-chats" aria-label={co.conversations}>
      <h2 className="cc-subheading">{co.conversations}</h2>
      {!rows.length && <p className="cc-company-empty">{co.noChats}</p>}
      {newestFirst(rows).map((row) => {
        const mark = chatMark(chatState(activities[row.id], runs[row.id], row.unread), activities[row.id]);
        const meta = [monthShort(row.period ?? ""), branchShort(row.scope ?? "")].filter(Boolean).join(" · ");
        return (
          <button
            key={row.id}
            type="button"
            className="cc-chat-row"
            data-slot="cc-company-chat"
            onClick={() => {
              setView("chat");
              void aui.threads().switchToThread(row.id);
            }}
          >
            <span className="cc-row-text">
              <Marquee className="cc-row-title">{row.title || t("untitled")}</Marquee>
              {meta && <Marquee className="cc-row-meta">{meta}</Marquee>}
            </span>
            {mark && <span className="cc-activity" data-activity={mark} role="img" aria-label={MARK_LABEL[mark]()} />}
            <span className="cc-chat-row-when">{relativeWhen(row.updated_at, now)}</span>
          </button>
        );
      })}
      {next && (
        <button type="button" className="cc-show-more" disabled={busy} onClick={() => void load(next)}>
          {co.showMore}
        </button>
      )}
    </section>
  );
};
