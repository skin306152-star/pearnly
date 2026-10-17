// 删除对话先确认一次（用户 10-17，同 Claude 手机 app 删对话先问）：标题「删除对话？」，多选「删除 N 个对话？」，
// 「取消 / 删除」，不加说明。四个入口（侧栏行「···」与手机长按、多选「删除 N」、电脑标题栏「···」、手机右上「…」）
// 和菜单里的 D 键都先过这里；样子同定时任务的删除确认（scheduled-tasks.tsx 的 Confirm）。
//
// 弹窗挂在页面根上（DeleteChatsHost，assistant.tsx）：点了菜单项菜单就关、侧栏行可能跟着卸载，问话不能挂在菜单里。
// 菜单关时把焦点还给触发钮，弹窗关时 Radix 再把焦点还回去；删除照旧按入口原来的做法执行（askDelete 只多问一次）。
import type { FC } from "react";
import { XIcon } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { create } from "zustand";

import { sb, t } from "@/i18n";

type Ask = { count: number; run: () => void };

const useAsk = create<{ ask: Ask | null }>(() => ({ ask: null }));

/** 先问一次再删：`count` 条对话，点「删除」才执行 `run`。 */
export function askDelete(count: number, run: () => void): void {
  // 等本次点击和菜单收起走完再开：从下拉菜单项里同步打开对话框，菜单收起时会把焦点抢回触发钮、弹窗一开就关
  requestAnimationFrame(() => useAsk.setState({ ask: { count, run } }));
}

/** 删这一条对话（行菜单、标题栏菜单）：点菜单那一刻就定下是哪条，问完再删时行和菜单可能已经卸载。 */
export function deleteChat(item: { delete: () => unknown }): void {
  askDelete(1, () => void item.delete());
}

export const DeleteChatsHost: FC = () => {
  const ask = useAsk((s) => s.ask);
  const close = () => useAsk.setState({ ask: null });
  return (
    <DialogPrimitive.Root open={!!ask} onOpenChange={(open) => !open && close()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[80] bg-overlay data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          data-slot="cc-delete-chats"
          className="cc-card cc-enter cc-modal fixed left-1/2 z-[80] flex w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 flex-col shadow-[var(--cc-shadow-pop)]"
        >
          <div className="flex shrink-0 items-center justify-between gap-3 px-5 pt-4 pb-2">
            <DialogPrimitive.Title className="cc-greeting min-w-0 text-xl">{ask ? sb.deleteAsk(ask.count) : ""}</DialogPrimitive.Title>
            <DialogPrimitive.Close className="cc-icon-btn" aria-label={t("close")}>
              <XIcon />
            </DialogPrimitive.Close>
          </div>
          <DialogPrimitive.Description className="sr-only">{ask ? sb.deleteAsk(ask.count) : ""}</DialogPrimitive.Description>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 px-5 pt-2 pb-4">
            <DialogPrimitive.Close asChild>
              <button type="button" className="cc-btn">{t("cancel")}</button>
            </DialogPrimitive.Close>
            <button
              type="button"
              className="cc-btn"
              data-danger=""
              data-slot="cc-delete-chats-yes"
              onClick={() => {
                const run = ask?.run;
                close();
                run?.();
              }}
            >
              {t("delete")}
            </button>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};
