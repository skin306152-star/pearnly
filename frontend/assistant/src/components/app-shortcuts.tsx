// 一小组键盘快捷键 + 快捷键表（11-09 对照审计 #9，截图 docs/work/2026-11-09-cc-vs-claude-ux-audit/claude-15）：
// 同 Claude 做「新建、搜索、开关侧栏、加文件、↑ 重用上一句、快捷键表」，其余不做。按键照 Claude：Ctrl/⌘ K 搜索、Ctrl/⌘ Shift O 新建、
// Ctrl/⌘ B 侧栏（组件库自带）、Ctrl/⌘ U 加文件、Ctrl/⌘ / 快捷键表；输入框是空的时 ↑ 把上一句放回输入框。
// 快捷键表照 Claude：标题、搜快捷键、「常规」「在会话中」两组，每行左边说明、右边按键（苹果设备显示 ⌘ ⇧ ↵）。
// 触屏设备没有这些键：不注册、不出表（同 components/shortcut.tsx）。

import { useEffect, useMemo, useState, type FC } from "react";
import { useAui } from "@assistant-ui/react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { useSidebar } from "@/components/ui/sidebar";
import { appleKeys, showShortcuts } from "@/components/shortcut";
import { OPEN_LAYERS } from "@/lib/layers";
import { t } from "@/i18n";
import { GENERAL, IN_CHAT, chord, filterRows, keyCaps, textOf } from "@/lib/shortcut-keys";

/** 挂在主框架里一次：注册快捷键、画快捷键表。onNew：两种模式各自的「新建会话」（同左上菜单那一项）。 */
export const AppShortcuts: FC<{ onNew: () => void }> = ({ onNew }) => {
  const aui = useAui();
  const { isMobile, open: sidebarOpen, setOpen: setSidebarOpen } = useSidebar();
  const [open, setOpen] = useState(false);
  const enabled = showShortcuts() && !isMobile;

  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.repeat || event.isComposing) return;
      const which = chord(event);
      if (which) {
        // 别的弹窗、菜单开着时只认「快捷键表」自己（表开着时同一个键关掉它）
        if (document.querySelector(OPEN_LAYERS) && !(which === "table" && open)) return;
        event.preventDefault();
        if (which === "table") return setOpen((now) => !now);
        if (which === "new") return onNew();
        if (which === "attach") return document.querySelector<HTMLButtonElement>(".aui-composer-add-attachment:not(:disabled)")?.click();
        // 搜索：侧栏收着先打开，再把光标放进侧栏顶上的搜索框
        if (!sidebarOpen) setSidebarOpen(true);
        requestAnimationFrame(() => document.querySelector<HTMLInputElement>('[data-slot="aui_thread-list-search"] input')?.focus());
        return;
      }
      // ↑：输入框是空的时，把上一句放回输入框（同 Claude）
      if (event.key !== "ArrowUp" || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
      const input = event.target as HTMLTextAreaElement | null;
      if (!input?.classList?.contains("aui-composer-input") || input.value !== "") return;
      const messages = aui.thread().getState().messages;
      const last = [...messages].reverse().find((message) => message.role === "user");
      const text = textOf(last as never);
      if (!text) return;
      event.preventDefault();
      aui.composer().setText(text);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [aui, enabled, onNew, open, sidebarOpen, setSidebarOpen]);

  if (!enabled) return null;
  return <ShortcutsDialog open={open} onOpenChange={setOpen} />;
};

/** 快捷键表本身（不读状态，测试直接画）。 */
export const ShortcutsDialog: FC<{ open: boolean; onOpenChange: (open: boolean) => void }> = ({ open, onOpenChange }) => {
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);
  const groups = useMemo(
    () =>
      [
        { title: t("shortcutsGeneral"), rows: filterRows(GENERAL, query) },
        { title: t("shortcutsInChat"), rows: filterRows(IN_CHAT, query) },
      ].filter((group) => group.rows.length),
    [query],
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined} className="cc-shortcuts max-w-(--cc-dialog-w-md) max-h-[calc(var(--cc-vh)*var(--cc-modal-share))]" data-slot="cc-shortcuts">
        <DialogTitle>{t("shortcutsTitle")}</DialogTitle>
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("shortcutsSearch")}
          aria-label={t("shortcutsSearch")}
        />
        <div className="cc-shortcuts-list">
          {groups.length === 0 && <p className="cc-shortcuts-empty">{t("noResults")}</p>}
          {groups.map((group) => (
            <section key={group.title} className="cc-shortcuts-group">
              <h3 className="cc-shortcuts-head">{group.title}</h3>
              {group.rows.map((row) => (
                <div key={row.keys} className="cc-shortcuts-row">
                  <span>{row.label()}</span>
                  <KbdGroup>
                    {keyCaps(row.keys, appleKeys()).map((cap, i) => (
                      <Kbd key={i}>{cap}</Kbd>
                    ))}
                  </KbdGroup>
                </div>
              ))}
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
};
