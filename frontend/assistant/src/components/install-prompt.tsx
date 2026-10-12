// 「把 CC 装到这台设备」的入口（lib/install.ts 决定装什么）：欢迎页一条可关掉的提示 + 用户菜单里一项（同 Claude 的「获取应用」）。
// iPhone 不能一键安装，点了给两步指引。样式在 globals.css 的 .cc-install / .cc-install-guide。

import { DownloadIcon, MonitorDownIcon, ShareIcon, SquarePlusIcon, XIcon } from "lucide-react";
import { Dialog as DialogPrimitive, DropdownMenu } from "radix-ui";
import { createContext, useContext, useState, type ReactNode } from "react";
import { t } from "@/i18n";
import { dismissInstall, install, installDismissed, type InstallTarget, useInstallTarget } from "@/lib/install";
import { useProfile } from "@/lib/profile";

const LABEL: Record<Exclude<InstallTarget, null>, () => string> = {
  desktop: () => t("getDesktopApp"),
  pwa: () => t("installApp"),
  ios: () => t("addToHome"),
};

function TargetIcon({ target }: { target: Exclude<InstallTarget, null> }) {
  if (target === "desktop") return <MonitorDownIcon />;
  if (target === "ios") return <SquarePlusIcon />;
  return <DownloadIcon />;
}

const GuideContext = createContext<() => void>(() => undefined);

/** 包住页面：提供 iPhone 指引对话框（菜单项、欢迎页提示共用一个）。 */
export function InstallProvider({ children }: { children: ReactNode }) {
  const [guide, setGuide] = useState(false);
  return (
    <GuideContext.Provider value={() => setGuide(true)}>
      {children}
      <DialogPrimitive.Root open={guide} onOpenChange={setGuide}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-[70] bg-overlay data-[state=open]:animate-in data-[state=open]:fade-in-0" />
          <DialogPrimitive.Content aria-describedby={undefined} className="cc-install-guide">
            <DialogPrimitive.Title className="cc-install-guide-title">{t("addToHome")}</DialogPrimitive.Title>
            <ol>
              <li>
                <span className="cc-install-guide-icon"><ShareIcon /></span>
                {t("addToHomeStep1")}
              </li>
              <li>
                <span className="cc-install-guide-icon"><SquarePlusIcon /></span>
                {t("addToHomeStep2")}
              </li>
            </ol>
            <DialogPrimitive.Close className="cc-btn self-end">{t("close")}</DialogPrimitive.Close>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </GuideContext.Provider>
  );
}

function useInstall() {
  const target = useInstallTarget(useProfile()?.product);
  const showGuide = useContext(GuideContext);
  const run = () => (target === "ios" ? showGuide() : void install(target));
  return { target, run };
}

/** 用户菜单里的一项；这台设备不用装（已装 / 在桌面版里）就不出现。 */
export function InstallMenuItem() {
  const { target, run } = useInstall();
  if (!target) return null;
  return (
    <DropdownMenu.Item className="cc-menu-item" onSelect={run}>
      <TargetIcon target={target} />
      <span className="flex-1">{LABEL[target]()}</span>
    </DropdownMenu.Item>
  );
}

/** 欢迎页上方一条：点它装，× 关掉后这台设备不再出现。 */
export function InstallBanner() {
  const { target, run } = useInstall();
  const [hidden, setHidden] = useState(installDismissed);
  if (!target || hidden) return null;
  return (
    <div className="cc-install cc-enter">
      <button type="button" className="cc-install-main" onClick={run}>
        <TargetIcon target={target} />
        {LABEL[target]()}
      </button>
      <button
        type="button"
        className="cc-install-close"
        aria-label={t("close")}
        onClick={() => {
          dismissInstall();
          setHidden(true);
        }}
      >
        <XIcon />
      </button>
    </div>
  );
}
