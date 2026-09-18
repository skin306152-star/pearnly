# ADR-007 前端皮肤：色值整份沿用 pearnly-app 的设计系统

状态：已选定（用户 2026-09-18："涉及到颜色皮肤 css 的地方，用 pearnly-app 的颜色"）。

## 背景

ADR-005 定了新业务页面用 Vue 3，但没说**长什么样**。而旧产品里已经有一份成熟的设计
系统：`pearnly-app/static/pearnly-ui.css`，自称 "Pearnly unified design system"，
被**五个入口共用**（旧构建脚本 `check_cachebust.py` 的共用清单就是它），
语义层注释写着 `Purple v2 = home's exact values` —— 它的色值就是当前产品线上真正的样子。

同时它自带一套组件类（`.pu-btn` / `.pu-modal` / `.pu-toast` / `.pu-empty` /
`.pu-error` / `.pu-skeleton`），与 ADR-005 选的 Frappe UI 是两套组件实现。

## 决策

### 1. 色值不自己定，整份沿用

| 令牌 | 亮色 | 暗色 |
|---|---|---|
| `--accent` / `--brand` | **`#7c4dff`** | `#a974ff` |
| `--accent-deep` / `--brand-hover` | `#6b3ff2` | `#c7a8ff` |
| `--accent-weak` | `#eee5ff` | `#2c2347` |
| `--accent-ink` | `#fff` | `#1d1438` |

中性色、状态色（`--good` / `--warn` / `--bad`）、Open Props 的 violet 与 gray 色阶、
间距、圆角、字号、阴影一并沿用。**逐字节搬进本仓**（`frontend/design/pearnly-ui.css`），
新界面才不会看起来像另一个产品。

搬迁规则：逐字节复制不手改；来源 SHA-256 记在 `frontend/design/README.md`；
要改先改旧仓源头再整份重搬。理由与登录页那套一样 —— **复用，不重画**。

### 2. 组件：Frappe UI 打底 + 这套令牌

ADR-005 选 Frappe UI 的理由不变（与 Frappe 生态同源、上游维护）。所以组件层用 Frappe UI，
**把它套上这份令牌的皮**：色值、圆角、控件高度节奏都从令牌取，不写死字面量。

这样两边的职责是清楚的：

| 管什么 | 谁管 |
|---|---|
| 颜色、圆角、间距、字号、阴影 | 本仓 `frontend/design/pearnly-ui.css`（沿用旧产品） |
| 控件行为、无障碍、表单结构 | Frappe UI |
| 品牌观感的一致性 | 令牌层，四个入口共用同一份 |

`.pu-*` 那些现成类不直接搬进 Vue 组件里用（会和 Frappe UI 形成两套并行实现，
升级时互相绊）。它们的价值是**提供参考实现与既定观感**，按需在 Frappe UI 上落成同样效果。

### 3. 三个紫不要搞混

| 值 | 出处 | 用在哪 |
|---|---|---|
| `#7c4dff` | 产品的设计系统 | **产品界面主色** |
| `#a974ff` | 同上，暗色层 | 暗色模式主色 |
| `#7655f6` | 旧登录页 `landing-*` 那一套 | 只属于登录页；那一屏已按其原设计整份复用 |

登录页是身份服务的主题（P4 已完成），不跟着产品皮肤走 —— 那本来就是两套设计，
用户当时也是分别确认的。

## 后果与约束

- **前端还没有工程**：本决策只把"色从哪来"定下来，Vue 工程、页面清单、
  翻译机制都还没建。ADR-005 的施工顺序（列页面清单 → 一条完整纵向切片）继续有效。
- **令牌只有一份**：四个入口（Cowork / ERP / DMS / 控制面）共用同一份令牌文件，
  不各copy一份改。控制面只在本机跑（ADR-004），但观感保持一致。
- **暗色模式**：令牌层支持（`.dark` / `[data-theme=dark]`），但本轮**不做暗色适配**，
  只用亮色；将来要开，改的是同一个令牌层，不用动组件。
- 登录页主题里那份 CSS 与这份令牌**互不依赖**：一个在身份服务容器里，一个在前端构建里。

## 参考

- 令牌与来源：[frontend/design/README.md](../../frontend/design/README.md)
- 前端技术选型：[ADR-005](0005-frontend-architecture.md)
