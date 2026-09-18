// 各产品的侧栏定义。
//
// 页面清单的权威来源见 docs/frontend/01-page-inventory.md（取自旧系统代码与线上截图）。
// 这里只描述"结构"，**不代表这家租户买了** —— 每项带 `feature` 的要用启动数据过滤，
// 没买的整组/整项不显示。真正的拦截在后端（没买的功能角色没发，接口会 403），
// 前端隐藏只是为了别让人点进死路。

export interface NavItem {
  label: string;
  route: string;
  /** 需要哪个功能；不给就是所有人都有 */
  feature?: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export type NavEntry = NavItem | NavGroup;

function isGroup(entry: NavEntry): entry is NavGroup {
  return "items" in entry;
}

const HOME: NavItem = { label: "首页", route: "/home" };

const NAV: Record<string, NavEntry[]> = {
  cowork: [
    HOME,
    { label: "工作协作", route: "/work", feature: "boards" },
    {
      label: "Pearnly Cowork",
      items: [
        { label: "录入工作台", route: "/intake", feature: "intake" },
        { label: "识别记录", route: "/history", feature: "ocr_history" },
        { label: "推送日志", route: "/push-logs", feature: "push_log" },
        { label: "对账中心", route: "/reconcile", feature: "recon" },
        { label: "库存盘点", route: "/stocktake", feature: "stocktake" },
      ],
    },
    {
      label: "主数据",
      items: [
        // 这几项属于 cowork 的 master_data 功能（见 features.py 的 CATALOG）。
        // 一开始漏了 feature，结果**没买主数据的租户也看得到**这组菜单 ——
        // 那样"按买到的功能显示菜单"就是假的（回归验证抓到）。
        { label: "客户管理", route: "/clients", feature: "master_data" },
        { label: "公司资料", route: "/company", feature: "master_data" },
        { label: "团队管理", route: "/team", feature: "master_data" },
        { label: "集成", route: "/integrations", feature: "master_data" },
      ],
    },
    { label: "使用教程", route: "/guide", feature: "express_guide" },
  ],
  erp: [
    HOME,
    {
      label: "商品与库存",
      items: [
        { label: "商品数据", route: "/products", feature: "products" },
        { label: "库存", route: "/inventory", feature: "warehouses" },
        { label: "入库", route: "/stock-in", feature: "warehouses" },
        { label: "出库", route: "/stock-out", feature: "warehouses" },
        { label: "收发存报表", route: "/stock-card", feature: "stockcard" },
      ],
    },
    {
      label: "采购系统",
      items: [
        { label: "采购记录", route: "/purchase", feature: "purchase" },
        { label: "供应商", route: "/suppliers", feature: "purchase" },
        { label: "采购设置", route: "/purchase-settings", feature: "purchase" },
      ],
    },
    {
      label: "销售系统",
      items: [
        { label: "销售记录", route: "/sales", feature: "sales" },
        { label: "客户", route: "/clients", feature: "clients" },
      ],
    },
    {
      label: "主数据",
      items: [
        { label: "公司资料", route: "/company" },
        { label: "团队管理", route: "/team" },
        { label: "集成", route: "/integrations" },
      ],
    },
  ],
  dms: [
    HOME,
    // 连接第三方 DMS 不设 feature：它是这个产品的地基，老产品也从不藏它。
    // 真正会挡住人的是后端（没配连接就推不动）。
    { label: "连接第三方 DMS", route: "/connect" },
    { label: "推送记录", route: "/records", feature: "push_record" },
    { label: "操作员", route: "/roster", feature: "roster" },
  ],
};

/** 按"买到的功能"过滤后的侧栏。整组都不显示时，那一组一起隐藏。 */
export function navFor(product: string, features: string[]): NavEntry[] {
  const entries = NAV[product] ?? [];
  const owned = new Set(features);
  const ok = (item: NavItem) => !item.feature || owned.has(item.feature);

  const out: NavEntry[] = [];
  for (const entry of entries) {
    if (!isGroup(entry)) {
      if (ok(entry)) out.push(entry);
      continue;
    }
    const items = entry.items.filter(ok);
    if (items.length) out.push({ label: entry.label, items });
  }
  return out;
}

/** 把侧栏摊平成 路由 → 标签 / 所需功能，给路由守卫用。 */
export function flatNav(product: string): Map<string, NavItem> {
  const map = new Map<string, NavItem>();
  for (const entry of NAV[product] ?? []) {
    if (isGroup(entry)) entry.items.forEach((item) => map.set(item.route, item));
    else map.set(entry.route, entry);
  }
  return map;
}

/**
 * 路由 → 页面标题，**不看买没买**。
 *
 * 为什么要有它：顶部那行标题原来是"从过滤后的侧栏里找当前项"，于是直接输地址
 * 进一个没买的功能（深链接、别人发来的链接）时，标题会变成"未建页面"—— 而
 * 页面明明是有的，只是菜单里不显示。标题属于"这个路由是什么"，不属于"我买到什么"。
 */
export function titleFor(route: string): string {
  for (const entries of Object.values(NAV)) {
    for (const entry of entries) {
      if (isGroup(entry)) {
        const hit = entry.items.find((item) => item.route === route);
        if (hit) return hit.label;
      } else if (entry.route === route) {
        return entry.label;
      }
    }
  }
  return "";
}
