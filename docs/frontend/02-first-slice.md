# 第一条前端纵向切片：产品壳 + 采购录入

日期：2026-09-18。状态：**已打通并实测**（13 项断言全过）。

## 这一刀切了什么

```text
登录 → 打开产品壳 → 左侧导航按"买到的功能"渲染
     → 进采购记录 → 建一张真实采购单 → 列表查回、汇总对账
```

它证明的不是"页面能打开"，而是**前后端合同成立**：授权由后端给、金额由后端算、
数据真的落了库。

## 目录与形状

```text
frontend/                        四个入口共用的前端
├── design/pearnly-ui.css        设计令牌（整份搬自旧产品，见 ADR-007）
├── src/
│   ├── lib/api.ts               调后端（白名单方法、错误解析、csrf）
│   ├── lib/shell.ts             启动数据：产品 / 买到的功能 / csrf
│   ├── lib/nav.ts               各产品侧栏定义 + 按授权过滤
│   ├── App.vue                  产品壳：侧栏 + 顶栏 + 主区
│   └── pages/                   home（占位）、purchase（已做）、NotBuilt（其余）
└── vite.config.ts
```

产物打到 `apps/pearnly_core/pearnly_core/public/pearnly-app/`，
经 Frappe 的 `/assets/pearnly_core/pearnly-app/` 提供。放 core 而不是各产品 App，
是因为四个入口共用同一份前端（ADR-005），各拷一份就成了四份要同步的东西。

## 三个关键约定（都是踩过的坑换来的）

| 约定 | 为什么 |
|---|---|
| 构建 `base` 必须是 `/assets/pearnly_core/pearnly-app/` | Frappe 只把 `/assets/<app>/…` 映射到 `apps/<app>/<app>/public/…`。用默认的 `/`，产物会写成 `/assets/index.<hash>.js`，那个 URL 会被当成"某个叫 index.<hash>.js 的 App"，直接 404 |
| csrf 由站点下发，不从 cookie 找 | Frappe 校验请求头与会话里存的值是否相等（`frappe/auth.py:81`）。会话里的值前端看不到，也**没有 csrf cookie** 可读 |
| 不做 dev server 代理 | 接口靠会话 cookie 认证。代理到另一个源之后，登录态、CSRF 规则都和线上不是一回事，在那里"验证通过"说明不了问题。本地开发用 `--watch` 构建 + 直接开站点域名 |

## 外壳用官方组件、业务内容用自己的（用户 2026-09-18 定）

> "侧栏的前端组件用官方的吧，作业业务内容用我们的。"

侧栏换成 **Frappe UI 的 `Sidebar`**（sections / header / footer 插槽 / 收起展开都是它自带的），
业务页面（采购那套卡片、表格、表单）仍是我们自己写的。

**颜色仍然全部是我们的**，做法是**接管官方组件的 CSS 变量**：

```text
Frappe UI 把颜色写成 CSS 变量：--surface-menu-bar / --ink-gray-6 / --outline-gray-1 …
我们在 src/styles/tailwind.css 里把这些变量指向 design/pearnly-ui.css 的令牌
→ 组件的行为与可访问性是官方的，观感是我们的（ADR-007）
```

没有去改它的组件，也没有把它的颜色调成我们的之后"顺便改改组件"——
只动变量，官方组件原样升级。

配套要装的东西（都不是可选的，缺一个组件就没样式）：Tailwind + `frappe-ui/tailwind` 的 preset、
`unplugin-icons`（组件源码里写着 `~icons/lucide/...`）。`content` 里必须扫
`node_modules/frappe-ui/src/**` —— 它是按需编译的、没有预构建产物，不扫就会把类名摇掉。

### 类型边界

frappe-ui 只发**源码**不发 d.ts，于是 `vue-tsc` 会进去检查它内部每个文件，
而它们在我们这套严格设置下有一批报错。处理办法是在 tsconfig 的 `paths` 里把
`frappe-ui` 指到我们自己的类型桩（`src/types/frappe-ui.ts`）：**类型检查走桩、构建走真包**
（Vite 不读 tsconfig 的 paths）。用了新组件就往桩里加一行。

### 尺寸与间距归官方，操作习惯归我们（用户 2026-09-18 定）

> "按钮大小，间距都用官方原生的，操作习惯还是用我们的。"

| | 归谁 | 怎么落 |
|---|---|---|
| 按钮/输入框的**尺寸、内边距、圆角、悬停态** | 官方 | 用官方 `Button` 组件；输入框用官方原生类 `form-input` |
| **配色** | 我们 | 官方 Button 只有 gray/blue/green/red 四个主题、没有我们的紫，所以加一个**只改配色**的类 `.pn-primary`（尺寸一概不碰） |
| **页面布局与操作流程** | 我们 | 表单分几列、按钮放哪、先做什么后做什么，都是我们的 |

实测值（验证脚本会断言，改了尺寸就失败）：按钮高 **28px**（官方 `sm` 档 h-7）、
内边距 **0 8px**、圆角 **8px**；官方原生输入框同样 **28px** 高。

原来那套手写的 `.pn-btn` / `.pn-input` 已经删掉 —— 自己定一套尺寸就是第二套设计语言，
与"用官方原生的"相反。

## 授权怎么走（这一条决定架构）

```text
开通时：控制面把"买到的功能码"写进租户站点配置 pearnly_features
运行时：前端问 /api/method/pearnly_core.shell.describe
        → 拿到 product + features + csrf
        → 按 features 过滤侧栏
拦截：  后端（没买的功能角色没发，对应 DocType 访问不了）
```

**前端隐藏菜单不是权限边界**，只是别让人点进死路。真正的拦截在后端 —— 这点必须一直成立。

为什么不从角色反推买到的功能：角色名是给人看的显示名（"ERP 采购"），拿它反解功能码
属于推断；而开通时本来就拿着准确的功能清单。权威仍在控制面的发号单上，
站点配置只是一份给站点自己看的投影。

## 实测证据

`scripts/sample-verify-frontend-slice.mjs`，**13 项全过**：

| 项 | 验到了什么 |
|---|---|
| 启动数据 | 产品=cowork、买到的功能、csrf 都有 |
| **菜单按授权** | 只买了 `home_subscription + intake` 的租户，侧栏就是「首页 / 录入工作台」；没买的 库存盘点 / 工作协作 / **主数据整组**都不显示 |
| **真建单** | ERP 租户建出 `PUR-2026-00001`，提示回显单号 |
| **列表查回** | 新建的供应商出现在列表里，条数从 0 → 1 |
| 金额 | 服务端算的 THB 386.81（3 × 120.5 + 7% 税），前端不算钱 |
| 隔离 | cowork 站点调 ERP 采购接口被挡（HTTP 417） |

截图：`docs/p4/evidence/frontend-{cowork-shell,erp-purchase}.png`。

## 回归验证抓到的两个问题（都已修）

1. **漏了功能门禁**：cowork 的"主数据"那几项一开始没写 `feature`，结果**没买主数据的
   租户也看得到那组菜单** —— 那样"按买到的功能显示菜单"就是假的。已补上 `master_data`，
   并加了一条"整组都不该出现"的断言。
2. **`frappe.installer.update_site_config` 会炸**：`frappe.installer` 不是自动挂上去的
   属性，得 `from frappe.installer import update_site_config`。更坑的是炸出来的报错会被
   `bench execute` 的 kwargs eval **吞掉**，换成一句无关的
   `NameError: name 'pearnly_core' is not defined`。

## 顺带打通的

- **真开通了一个 ERP 租户**（发号单 `ISS-2026-0054` → `qerp124510.localhost`），
  走的是完整开通流程。顺带确认了新代码在流程里都生效：
  `finalize_site` 返回 `language: "th"`（ADR-006）、`set_workspace` 落下了买到的功能、
  `set_roles` 建了角色并拿走了 System Manager。
- 前端产物走**目录挂载**进容器（`infrastructure/compose/01-bench.yml`），
  改前端不用重建镜像（原来每改一行要等 25 秒）。镜像里仍然会打进同一份产物，
  所以生产不依赖这个挂载。

## 边界（没做 / 不确定）

- **只在本机验证过**，没有部署、没有真机验收。
- **入口还没切**：产品壳现在要通过 `/assets/pearnly_core/pearnly-app/index.html` 打开，
  站点根路径仍然落在 Desk 工作台。等页面多做几个再切，现在切过去反而更难用。
- **首页（订阅与计费）只是占位**：新平台没有订阅/计费后端服务（全仓搜不到），
  做不出可用界面。旧系统里那一页有两套各写各的实现（产品壳 303 行 / DMS 1747 行），
  合并时要先定后端以哪套为准。
- **采购之外的其他页面**都落到 NotBuilt 占位页（菜单照清单列出，点进去如实说没做）。
- **没有自动化测试**：目前只有这个端到端验证脚本，没有单元测试。
- **CSRF 校验的另一条路没验**：Frappe 在 `Referer/Origin` 命中允许列表时会跳过 csrf 校验
  （`auth.py:92`）。我们是显式带 token 的，那条路没依赖也不打算依赖，但也没专门验过它。
