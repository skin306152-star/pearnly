// 删除对话先确认一次（用户 10-17，同 Claude 手机 app 删对话先问）：标题「删除对话？」，多选「删除 N 个对话？」，
// 「取消 / 删除」，不加说明。四个入口（侧栏行「···」与手机长按、多选「删除 N」、电脑标题栏「···」、手机右上「…」）
// 和菜单里的 D 键都先过这里；样子同定时任务的删除确认（scheduled-tasks.tsx 的 Confirm）。
//
// 弹窗挂在页面根上（DeleteChatsHost，assistant.tsx）：点了菜单项菜单就关、侧栏行可能跟着卸载，问话不能挂在菜单里。
// 删除照旧按入口原来的做法执行（askDelete 只多问一次）。
//
// 读屏与焦点（第 11 轮 E37）：只有标题，不另挂说明（aria-describedby 不指向和标题一样的隐藏说明，读屏不念两遍）。打开时焦点在
// 「取消」（删除类确认的惯例：最不具破坏性的一项）。取消、Esc、×、点遮罩关掉：焦点回到打开它的地方——点菜单项那一刻（弹窗在下一帧
// 才开，那时菜单已关）记下那个菜单的触发处：侧栏行的菜单回到那一行（多选菜单同样回到开菜单的那一行），标题栏「···」、手机「…」回到
// 那个按钮。删了：侧栏里删掉的下一条（没有就上一条）；删的是正开着的对话（回到新对话页）落在输入框。
import { useRef, type FC } from "react";
import { useAui } from "@assistant-ui/react";
import { XIcon } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { create } from "zustand";

import { focusComposer } from "@/components/assistant-ui/projects";
import { sb, t } from "@/i18n";
import { rowOrder } from "@/lib/sidebar-keys";

const ROW = '[data-slot="aui_thread-list-item"]';
const ROW_TRIGGER = '[data-slot="aui_thread-list-item-trigger"]';
const SIDEBAR = '[data-slot="aui_thread-list-root"]';

/** 关掉后焦点回到哪：那个元素；侧栏行再记行号（行重画换了元素时按行号找回）。 */
export type Back = { el: HTMLElement | null; row: string };

type Ask = { ids: readonly string[]; run: () => unknown; back: Back };

const useAsk = create<{ ask: Ask | null }>(() => ({ ask: null }));

/** 打开删除确认的地方（点菜单项那一刻调）：菜单项 → 这个菜单的触发处（Radix 菜单的 aria-labelledby 指向触发钮）；在侧栏行里的
 *  换成行本身（行尾「···」不进 Tab 顺序，行才是键盘停的地方）。 */
export function backTarget(active: Element | null = document.activeElement): Back {
  const from = active && active !== document.body ? (active as HTMLElement) : null;
  const labelled = from?.closest<HTMLElement>('[role="menu"]')?.getAttribute("aria-labelledby");
  const trigger = (labelled ? document.getElementById(labelled) : null) ?? from;
  const row = trigger?.closest<HTMLElement>(ROW);
  return { el: row?.querySelector<HTMLElement>(ROW_TRIGGER) ?? trigger, row: row?.dataset.rowId ?? "" };
}

/** 先问一次再删：`ids` 是要删的对话（本页编号），点「删除」才执行 `run`。 */
export function askDelete(ids: readonly string[], run: () => unknown): void {
  // 点菜单项这一刻记下触发处：弹窗下一帧才开，那时菜单已关，焦点已不在菜单里
  const back = backTarget();
  // 等本次点击和菜单收起走完再开：从下拉菜单项里同步打开对话框，菜单收起时会把焦点抢回触发钮、弹窗一开就关
  requestAnimationFrame(() => useAsk.setState({ ask: { ids, run, back } }));
}

/** 删这一条对话（行菜单、标题栏菜单）：点菜单那一刻就定下是哪条，问完再删时行和菜单可能已经卸载。 */
export function deleteChat(item: { delete: () => unknown; getState: () => { id: string } }): void {
  askDelete([item.getState().id], () => item.delete());
}

/** 删完焦点放哪一行：侧栏里删掉的那几条之后的第一条，没有就之前最近的一条；侧栏里看不到它们（收起、手机抽屉关着）回 ""。 */
export function rowAfterDelete(order: readonly string[], ids: readonly string[]): string {
  const gone = new Set(ids);
  const first = order.findIndex((id) => gone.has(id));
  if (first < 0) return "";
  return order.slice(first).find((id) => !gone.has(id)) ?? [...order.slice(0, first)].reverse().find((id) => !gone.has(id)) ?? "";
}

function focusRow(row: string): boolean {
  const el = row ? document.querySelector<HTMLElement>(`${ROW}[data-row-id="${CSS.escape(row)}"] ${ROW_TRIGGER}`) : null;
  el?.focus();
  return !!el;
}

function focusBack(back: Back | undefined) {
  if (!back) return;
  if (back.el?.isConnected) back.el.focus();
  else focusRow(back.row);
}

export const DeleteChatsHost: FC = () => {
  const aui = useAui();
  const ask = useAsk((s) => s.ask);
  const cancel = useRef<HTMLButtonElement>(null);
  // 正在问的（关掉以后 ask 已清空，焦点归还还要用它记下的触发处）与删了以后焦点去哪（没删是 null）
  const asked = useRef<Ask | null>(null);
  if (ask) asked.current = ask;
  const after = useRef<(() => void) | null>(null);
  const close = () => useAsk.setState({ ask: null });
  const confirm = () => {
    const current = asked.current;
    if (!current) return;
    const main = aui.threads().getState().mainThreadId;
    if (current.ids.includes(main)) {
      // 删的是正开着的对话：组件库先切到新对话再删，切过去以后焦点进输入框
      after.current = () => {
        let frames = 0;
        const wait = () => {
          if (aui.threads().getState().mainThreadId !== main || ++frames > 120) focusComposer(false);
          else requestAnimationFrame(wait);
        };
        requestAnimationFrame(wait);
      };
    } else {
      const next = rowAfterDelete(rowOrder(document.querySelector(SIDEBAR)), current.ids);
      // 删掉的那几条这一帧还在侧栏里，下一帧（已移走）再放；侧栏里没有可去的（收起、抽屉关着）就回打开它的地方（在删掉的行里的
      // 已跟着移走），那里也没了就进输入框
      after.current = () =>
        requestAnimationFrame(() => {
          if (focusRow(next)) return;
          if (current.back.el?.isConnected) current.back.el.focus();
          else focusComposer(false);
        });
    }
    close();
    current.run();
  };
  return (
    <DialogPrimitive.Root open={!!ask} onOpenChange={(open) => !open && close()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[80] bg-overlay data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          data-slot="cc-delete-chats"
          aria-describedby={undefined}
          className="cc-card cc-enter cc-modal fixed left-1/2 z-[80] flex w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 flex-col shadow-[var(--cc-shadow-pop)]"
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            cancel.current?.focus();
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const go = after.current;
            after.current = null;
            if (go) go();
            else focusBack(asked.current?.back);
          }}
        >
          <div className="flex shrink-0 items-center justify-between gap-3 px-5 pt-4 pb-2">
            <DialogPrimitive.Title className="cc-greeting min-w-0 text-xl">{ask ? sb.deleteAsk(ask.ids.length) : ""}</DialogPrimitive.Title>
            <DialogPrimitive.Close className="cc-icon-btn" aria-label={t("close")}>
              <XIcon />
            </DialogPrimitive.Close>
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 px-5 pt-2 pb-4">
            <DialogPrimitive.Close asChild>
              <button ref={cancel} type="button" className="cc-btn" data-slot="cc-delete-chats-no">{t("cancel")}</button>
            </DialogPrimitive.Close>
            <button type="button" className="cc-btn" data-danger="" data-slot="cc-delete-chats-yes" onClick={confirm}>
              {t("delete")}
            </button>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};
