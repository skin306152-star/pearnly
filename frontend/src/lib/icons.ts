// 导航图标：用官方图标集（lucide），跟 Frappe UI 组件本身用的是同一套。
//
// 用 route 映射而不是写在 nav.ts 里：nav.ts 要能被非组件的逻辑用（比如路由守卫），
// 不该把组件依赖引进去。
import type { Component } from "vue";
import IconHome from "~icons/lucide/house";
import IconBoard from "~icons/lucide/kanban";
import IconIntake from "~icons/lucide/file-input";
import IconHistory from "~icons/lucide/history";
import IconPush from "~icons/lucide/send";
import IconRecon from "~icons/lucide/scale";
import IconStocktake from "~icons/lucide/clipboard-check";
import IconClients from "~icons/lucide/users";
import IconCompany from "~icons/lucide/building-2";
import IconTeam from "~icons/lucide/user-cog";
import IconIntegrations from "~icons/lucide/plug";
import IconGuide from "~icons/lucide/book-open";
import IconProducts from "~icons/lucide/package";
import IconInventory from "~icons/lucide/boxes";
import IconStockIn from "~icons/lucide/package-plus";
import IconStockOut from "~icons/lucide/package-minus";
import IconStockCard from "~icons/lucide/table-2";
import IconPurchase from "~icons/lucide/shopping-cart";
import IconSuppliers from "~icons/lucide/truck";
import IconSettings from "~icons/lucide/settings-2";
import IconSales from "~icons/lucide/receipt";
import IconRecords from "~icons/lucide/list";
import IconRoster from "~icons/lucide/id-card";
import IconDot from "~icons/lucide/circle";

const BY_ROUTE: Record<string, Component> = {
  "/home": IconHome,
  "/work": IconBoard,
  "/intake": IconIntake,
  "/history": IconHistory,
  "/push-logs": IconPush,
  "/reconcile": IconRecon,
  "/stocktake": IconStocktake,
  "/clients": IconClients,
  "/company": IconCompany,
  "/team": IconTeam,
  "/integrations": IconIntegrations,
  "/guide": IconGuide,
  "/products": IconProducts,
  "/inventory": IconInventory,
  "/stock-in": IconStockIn,
  "/stock-out": IconStockOut,
  "/stock-card": IconStockCard,
  "/purchase": IconPurchase,
  "/suppliers": IconSuppliers,
  "/purchase-settings": IconSettings,
  "/sales": IconSales,
  "/records": IconRecords,
  "/roster": IconRoster,
};

export function iconFor(route: string): Component {
  return BY_ROUTE[route] ?? IconDot;
}
