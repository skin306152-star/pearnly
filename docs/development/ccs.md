# ccs：CC 的样式与交互规范

CC 对话页（`frontend/assistant`）固定用这一套。新界面先套这份，再写组件；与它冲突的旧做法按这里改。

ccs = **Anthropic 官方公开的 Claude 样式变量** + **官方没覆盖的 Claude 外壳实测值** + **已收集的 Claude 交互清单**。

## 1. 取值：只在 `globals.css`

`frontend/assistant/src/globals.css` 是唯一来源，分三层：

| 层 | 变量 | 来源 |
| :- | :- | :- |
| 官方 | `--ccs-*`：背景、文字、边线、聚焦环、info / danger / success / warning、圆角 4/6/8/10/12/full、细线 0.5px、阴影 hairline/sm/md/lg、字号行高 | [官方 Style variables](https://claude.com/docs/connectors/building/mcp-apps/design-guidelines#style-variables) 照录，浅色 `:root`、深色 `.dark` |
| 外壳实测 | 主区与侧栏底色、侧栏宽、输入框圆角 20 与阴影、用户气泡圆角 18、手机外壳 | 官方表不管 Claude 自己的外壳；沿用截图实测，变量旁注明「外壳实测」 |
| 品牌 | `--cc-brand` 橘黄 #F28C28 | 只做强调（发送键、在动的小点），结构色一律用官方 |

- 组件用的是 `--cc-*` 名字（`--cc-ink`、`--cc-line`、`--cc-danger` …），它们引用 `--ccs-*`；Tailwind 的 `text-ink`、`bg-surface`、`border`（0.5px）、`rounded-md`（8）、`shadow-sm`、`text-sm`（14/1.4）都已接到这些变量。
- 字体保留 CC 现有的（官方 Anthropic Sans / Serif 只给本地开发下载）。
- 动效时长与缓动（`--cc-dur-*`、`--cc-ease*`）也在同一文件。
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
