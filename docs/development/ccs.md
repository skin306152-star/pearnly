# ccs：CC 的样式与交互规范

CC 对话页（`frontend/assistant`）固定用这一套。新界面先套这份，再写组件；与它冲突的旧做法按这里改。

ccs = **Anthropic 官方公开的 Claude 样式变量** + **Claude 设计系统实测值**（Claude Code 本身，2026-10-15 夜实测）+ **CC 品牌强调色** + **已收集的 Claude 交互清单**。

完整的实测规格与组件行为见 [CC 外壳设计](../work/2026-10-16-cc-shell-design.md)（取代 [研究文档](../work/2026-10-15-claude-code-ui-study.md) 的数值）。

## 1. 取值：只在 `globals.css`

`frontend/assistant/src/globals.css` 是唯一来源，分三层：

| 层 | 变量 | 来源 |
| :- | :- | :- |
| 官方 | `--ccs-*`：背景、文字、边线、聚焦环、info / danger / success / warning、圆角 4/6/8/10/12/full、细线 0.5px、阴影 hairline/sm/md/lg、字号行高 | [官方 Style variables](https://claude.com/docs/connectors/building/mcp-apps/design-guidelines#style-variables) 照录，浅色 `:root`、深色 `.dark`；**原样保留，不改** |
| Claude 设计系统实测 | `--code-*`：文字主 / 次 / 淡 / 禁用、表面 0–3 与弹层、侧栏底（网页 / 桌面窗口）、叠色 1–3、幽灵按钮与侧栏行的悬停 / 选中、线 / 强线 / 更强、链接、行内代码、焦点、遮罩、提示框；字号阶（12/17 … 24/31）、控件高、圆角、间距、图标、侧栏与菜单尺寸；卡片 / 悬浮 / 弹出 / 输入框阴影；缓动与时长 | [CC 外壳设计](../work/2026-10-16-cc-shell-design.md) 第 2 节逐项照录（浅色 `:root`、深色 `.dark`），`tests/test_cc_ccs.py` 逐项比对 |
| 品牌 | `--cc-brand` 橘黄 #F28C28 | 只做强调（发送键、在动的小点），结构色一律用设计系统 |

- 组件用的是 `--cc-*` 名字：结构色（`--cc-ink*`、`--cc-bg-*`、`--cc-line*`、`--cc-row-*`、`--cc-link`、`--cc-code-*`）、尺寸、阴影、动效引用 `--code-*`；状态色（`--cc-danger` …）、细线宽、圆角档位、官方阴影、字号表引用 `--ccs-*`。Tailwind 的 `text-ink`、`bg-surface`、`bg-row-hover`、`text-link`、`border`（0.5px）、`rounded-md`（8）、`rounded-code`（5）、`shadow-pop`、`shadow-focus`、`text-sm`（14/1.4）都已接到这些变量。
- 少数官方表与设计系统都没覆盖的手机外壳值（抽屉、圆按钮、手机输入框）沿用截图实测，变量旁注明「外壳实测」。
- 页面底 = 表面 1（浅 `#fcfcfb` / 深 `#151515`），电脑、手机、桌面窗口同一个；抄在别处的同色（`pearnly_core/www/assistant.html` 启动画面与 theme-color、`assistant/api.py` 离线页、`scripts/build-ios-splash.py` 启动图、`companion/…/gui_tomo.py` 的 `APP_BG`、`vite.config.ts` 的 manifest）跟着改，`tests/test_cc_ccs.py` 核对。
- 焦点一律 `--cc-shadow-focus`（内描 1px 页面色 + 外 1px `#2a78d6` 描边，**没有光晕**：用户 2026-10-16「全局这种光晕取消掉，可以用边框，但是不能有光晕」），按钮、输入框、行、搜索框、卡片同一个；32 的标题栏与侧栏搜索框用 1.5px 里描边（`--cc-shadow-focus-inset`），就地改名 1.5px 描边；不用 ring 光晕（`ring-[3px]`、半透明 ring、`ring-offset`），`tests/test_cc_ccs.py` 拦。
- 字体保留 CC 现有的（官方 Anthropic Sans / Serif 只给本地开发下载）；对话正文字号行高 14/20（手机 16/24），字体仍按设置「对话字体」。
- 动效：主缓动 `cubic-bezier(.32,.72,0,1)`、快 `(.2,0,0,1)`、淡出 `(.165,.84,.44,1)`，时长 60 / 120 / 200 / 300 / 450ms；悬停揭示 120ms（`--cc-reveal`）。

### 变体与密度

`lib/variant.ts` 启动时在 `<html data-cc-variant>` 写一次（窄屏分界与手机外壳同一条 `hooks/use-mobile.ts`，窗口跨过分界时跟着换）：

| 变体 | 何时 | 侧栏 | 行高 | 组上间距 | 其他 |
| :- | :- | :- | :- | :- | :- |
| `web` | 浏览器、电脑上装的网页应用 | 贴边，底 `#fafaf9` / 深色页面底 80% 混黑 | 32 | 16 | — |
| `desktop` | CC 桌面版窗口（`isDesktopApp()`） | 浮成卡片，离窗口边 8px，白卡 / `#262626`，卡片阴影 | 30 | 14 | — |
| `phone` | 窄屏（< 768） | 抽屉 | 44 | — | 组标题带 44、组箭头与组操作常显、正文 16/24 |

- 只有舒适档（没有密度设置入口，不写紧凑档）。行字 14/20、组标题 13/16 淡色（鼠标在侧栏里变次要色）、行尾控件 24（圆角 6）、导航图标 20 放在 28 的前导槽、行圆角 8。
- 侧栏行悬停 / 选中：浅色 `#f0efec` / `#edece8`，深色白 7.5% / 15%；**会话列表里选中 = 悬停色 + 主色字**，悬停不改字色。
- 菜单：圆角 12、内边距 4、最小宽 128、弹层底 + 1px 内环 + 弹出阴影；项高 32、圆角 8、内边距 6/10；分隔线 1px 左右内缩 10、上下 4；右侧当前值 13px 淡色（`.cc-menu-value`）；不可用 40%。放不下时按完整高度翻到另一边（`components/ui/menu.tsx` 的 `pickSide`），两边都放不下取大的一边在菜单里滚动；页面限高写 `[--cc-menu-max:…]`。
- 闸门 `python3 scripts/check-frontend-tokens.py`：组件与 lib 里不许写死颜色（#hex、`rgb()/hsl()`、Tailwind 调色板类）、圆角、阴影数值，只能引用变量或接到变量的工具类；没有忽略清单。确需新档位，先在 `globals.css` 的 `@theme` 接到变量。

## 2. 对话里的卡片

照官方 inline card 规矩（允许卡片、文件卡片、提醒卡片、步骤详情都适用）：

- 按钮最多 2 个，放在卡片底部；
- 数据最多 4–5 项，多了列前几项 + 「还有 N 项」；
- 高度随内容，**卡内不套滚动**；长内容先收起，点一下整段展开；
- 加载用骨架（`Skeleton`），不用转圈；
- 手机点按区不小于 44pt：能点的元素加 `cc-tap`（粗指针时最小 44×44）；
- 不在卡内放菜单 / 弹出层，用看得见的按钮；颜色、圆角只用变量。

## 3. 已收集的 Claude 交互

- **侧栏**：顶部「新建」；「已置顶」一组在最上；对话按项目 / 公司分组，组头点击折叠（折叠状态记在本机，搜索时全展开，手机上不折叠），组头旁「+」在该组里新开；每行悬停出「…」行菜单，手机长按弹同一菜单（不触发系统选字）；跑着的行尾转圈、跑完没看过一个蓝点。
- **工作状态行**（`thread.tsx` 的 RunIndicator）：按下发送即出现在用户消息下；橘色小点 + 本地每秒走的计时；在跑某一步时说进行时（「正在读取 xxx」）；等用户点「允许」时收起，点完再出来；在跑却 30 秒收不到服务端任何消息时改说「连接中断，正在重连」；直到这一轮真正结束才消失。
- **跟随滚动**（`lib/follow-scroll.ts`）：回复在出时跟到底；用户往上翻立刻停止跟随、不被拉回；回到离底部 48px 内且过了 400ms 冷却再接着跟。
- **输入法回车**（`lib/ime.ts`）：选字中的回车（`isComposing`，以及 Safari 结束选字时的 keyCode 229）不发送。

## 4. 参考边界

- 取值一律以官方文档为准；社区逆向仓库（如 github.com/Caho1/open-claude-ui）只当行为参考，不拷代码、不引组件、不加依赖。
- 视觉改动要在浅色 / 深色 × 电脑 / 手机各看一遍再交付。
