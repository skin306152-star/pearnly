// 「把 CC 装到这台设备」的入口（lib/install.ts 决定装什么）：欢迎页一条可关掉的提示 + 用户菜单里一项（同 Claude 的「获取应用」）。
// 按钮文字和图标跟着设备走；浏览器调不起安装（iPhone、Mac Safari）时点了给它自己菜单里的准确步骤。
// 样式在 globals.css 的 .cc-install / .cc-install-guide。

import {
  AppWindowMacIcon,
  ChevronDownIcon,
  DockIcon,
  DownloadIcon,
  ExternalLinkIcon,
  MenuIcon,
  MonitorDownIcon,
  ShareIcon,
  SquarePlusIcon,
  XIcon,
} from "lucide-react";
import { Dialog as DialogPrimitive, DropdownMenu } from "radix-ui";
import { createContext, useContext, useState, type ReactNode } from "react";
import { t } from "@/i18n";
import {
  canPrompt,
  dismissInstall,
  install,
  installDismissed,
  installGuide,
  type InstallGuide,
  type InstallTarget,
  useInstallTarget,
} from "@/lib/install";
import { useProfile } from "@/lib/profile";

type Target = Exclude<InstallTarget, null>;

const LABEL: Record<Target, () => string> = {
  desktop: () => t("getDesktopApp"),
  pwa: () => t("installApp"),
  mac: () => t("addToDock"),
  ios: () => t("addToHome"),
  browser: () => t("openInBrowser"),
};

const ICON: Record<Target, typeof DownloadIcon> = {
  desktop: MonitorDownIcon,
  pwa: DownloadIcon,
  mac: DockIcon,
  ios: SquarePlusIcon,
  browser: ExternalLinkIcon,
};

// 每套步骤：[图标, 文案]；图标画成浏览器里那个按钮的样子，照着找
const STEPS: Record<InstallGuide, [typeof DownloadIcon, () => string][]> = {
  "safari-menu": [
    [MenuIcon, () => t("guideSafariMenu")],
    [ChevronDownIcon, () => t("guideViewMore")],
    [SquarePlusIcon, () => t("addToHomeStep2")],
  ],
  "chrome-ios": [
    [ShareIcon, () => t("guideChromeShare")],
    [ChevronDownIcon, () => t("guideViewMore")],
    [SquarePlusIcon, () => t("addToHomeStep2")],
  ],
  share: [
    [ShareIcon, () => t("addToHomeStep1")],
    [SquarePlusIcon, () => t("addToHomeStep2")],
  ],
  "mac-safari": [
    [AppWindowMacIcon, () => t("guideMacFile")],
    [DockIcon, () => t("guideMacDock")],
  ],
};

const GuideContext = createContext<() => void>(() => undefined);

/** 包住页面：提供「添加」步骤对话框（菜单项、欢迎页提示共用一个）。 */
export function InstallProvider({ children }: { children: ReactNode }) {
  const [guide, setGuide] = useState<InstallGuide | null>(null);
  return (
    <GuideContext.Provider value={() => setGuide(installGuide())}>
      {children}
      <DialogPrimitive.Root open={guide !== null} onOpenChange={(open) => !open && setGuide(null)}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-[70] bg-overlay data-[state=open]:animate-in data-[state=open]:fade-in-0" />
          <DialogPrimitive.Content aria-describedby={undefined} className="cc-install-guide">
            <DialogPrimitive.Title className="cc-install-guide-title">
              {guide === "mac-safari" ? t("addToDock") : t("addToHome")}
            </DialogPrimitive.Title>
            <ol>
              {guide &&
                STEPS[guide].map(([Icon, text]) => (
                  <li key={text()}>
                    <span className="cc-install-guide-icon"><Icon /></span>
                    {text()}
                  </li>
                ))}
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
  // iPhone 一律给步骤；Mac 上 Chrome、Edge 能一键，Safari 给步骤
  const run = () => (target === "ios" || (target === "mac" && !canPrompt()) ? showGuide() : void install(target));
  return { target, run };
}

/** 用户菜单里的一项；这台设备不用装（已装 / 在桌面版里）就不出现。 */
export function InstallMenuItem() {
  const { target, run } = useInstall();
  if (!target) return null;
  const Icon = ICON[target];
  return (
    <DropdownMenu.Item className="cc-menu-item" onSelect={run}>
      <Icon />
      <span className="flex-1">{LABEL[target]()}</span>
    </DropdownMenu.Item>
  );
}

/** 欢迎页上方一条：点它装，× 关掉后这台设备不再出现。 */
export function InstallBanner() {
  const { target, run } = useInstall();
  const [hidden, setHidden] = useState(installDismissed);
  if (!target || hidden) return null;
  const Icon = ICON[target];
  return (
    <div className="cc-install cc-enter">
      <button type="button" className="cc-install-main" onClick={run}>
        <Icon />
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
