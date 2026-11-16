// 业务菜单的图标：声明里写的 lucide 名（pearnly_core/assistant/guide/declaration.py 的 ICONS 白名单）→ 组件。
// 白名单加图标时这里同时加（引擎窗口管白名单）；不认识的名字退回按业务 id 的老三个，再退回空。
// 待定：本仓锁定的 lucide-react 版本没有 building-plus，暂用 building-2，等引擎窗口换一个本版本有的名字。
import {
  BoxesIcon, Building2Icon, BuildingIcon, ClipboardListIcon, DownloadIcon, FileSpreadsheetIcon, FolderClockIcon, KanbanIcon, LandmarkIcon,
  ListChecksIcon, type LucideIcon, PercentIcon, ReceiptIcon, ScaleIcon, SettingsIcon, ShoppingCartIcon, StoreIcon, TagIcon, WalletIcon,
} from "lucide-react";
import type { FC } from "react";

export const BUSINESS_ICONS: Record<string, LucideIcon> = {
  "shopping-cart": ShoppingCartIcon, tag: TagIcon, wallet: WalletIcon, landmark: LandmarkIcon, scale: ScaleIcon, store: StoreIcon,
  "clipboard-list": ClipboardListIcon, receipt: ReceiptIcon, percent: PercentIcon, download: DownloadIcon, "folder-clock": FolderClockIcon,
  "list-checks": ListChecksIcon, building: BuildingIcon, kanban: KanbanIcon, settings: SettingsIcon, boxes: BoxesIcon,
  "file-spreadsheet": FileSpreadsheetIcon, "building-plus": Building2Icon,
};
const BY_BUSINESS: Record<string, string> = { purchase: "shopping-cart", sales: "tag", collection: "wallet" };

export const BusinessIcon: FC<{ business: string; icon?: string }> = ({ business, icon }) => {
  const Icon = BUSINESS_ICONS[icon ?? ""] ?? BUSINESS_ICONS[BY_BUSINESS[business] ?? ""];
  return Icon ? <Icon /> : null;
};
