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
- 闸门 `python3 scripts/check-frontend-tokens.py`：R1–R3 颜色、圆角、阴影全仓失败；R4–R15 尺寸、间距、字号与行高、层级、动效、其他数值类、任意值与 `!`、断点与输入设备变体、JSX 数值与内联 style、globals.css 自身、`--cc-*` 必须已定义、字重——在 `scripts/frontend-tokens-strict.txt`（已完成清单，不是豁免清单）里的文件失败，范围外只报数（`--report`），B8 起全仓失败。没有忽略清单、不支持行内禁用。判据见 [标准化记录](../work/2026-10-17-cc-frontend-standards.md) 1.2、3.1。

### 尺寸、间距、字号、层级、动效（B1 起）

数值只在 `globals.css`，这里只写规则和名字，**数值看 globals.css**。

| 层 | 名字 | 内容 | 能不能改 |
| :- | :- | :- | :- |
| 官方 | `--ccs-*` | Anthropic 公开的样式变量 | 原样照录，不改 |
| 实测 | `--code-*` | Claude 设计系统实测 | 由 `tests/test_cc_ccs.py` 逐项比对，不改 |
| 刻度 | `--cc-space-*`、`--cc-control-*`、`--cc-icon-*`、`--cc-radius-*`、`--cc-text-*`、`--cc-weight-*`、`--cc-z-*`、`--cc-dur-*`、`--cc-disabled-opacity` | 组件可用的全部档位（globals.css「刻度层」一块，同一个定义只出现一次） | 只引用上面两层或注明出处 |
| 用途 | `--cc-row-*`、`--cc-menu-*`、`--cc-dialog-*`、`--cc-settings-*`、`--cc-thread-*`、`--cc-cell-*` … | 有业务含义、可能按变体切换的值 | 优先引用刻度层；不在刻度上的值注明出处 |
| 运行时 | `--cc-vvh`、`--cc-vvtop`、`--cc-marquee-x / -dur`、`--cc-modal-share`、`--cc-menu-max` | 脚本或组件运行时写入 | 登记默认值，注释写「运行时写入」和写入方 |
| 变体 | `:root[data-cc-variant=web\|desktop\|phone]`、`:root[data-cc-touch]` | 只覆盖用途层变量与点按区 | — |

- 刻度名：间距 `--cc-space-2 / 4 / 6 / 8 / 12 / 16 / 24 / 28 / 40`（按像素命名）；控件 `--cc-control-2xs / nested / xs / sm / (无后缀) / md / lg` 与 `--cc-tap`；图标 `--cc-icon-2xs / xs / sm / md / (无后缀) / xl / 2xl` 与 `--cc-icon-stroke`；层级 `--cc-z-raise / float / bar / scrim / sheet / drag-shield / drag / menu / modal / modal-top / confirm / tooltip`；时长 `--cc-dur-fast / (无后缀) / slow / pop`；字重 `--cc-weight-regular / medium / semibold / bold`。10、14、20、30、36 这类不在刻度上的值只经用途层变量进入组件，不为凑整挪到相近的刻度。
- 出处注释（写在变量同一行，闸门查）：实测（引用 `--code-*`）、外壳实测、系统值、现有值·CC 自定（没有 Claude 参照，照现值登记，等有参照再改）；另有「运行时写入」「照搬宣传页」（形象动画）。
- 组件里怎么写：外观放在 `cc-*` 类里，组件只写布局，外加变量写法（`p-(--cc-space-12)`、`size-(--cc-icon-sm)`、`z-(--cc-z-menu)`、`duration-(--cc-dur)`）；字号用 `@theme` 接过的实测字阶 `text-caption / footnote / group / panel / body / subhead / prose / title / page`（官方 `text-lg / xl / 2xl / 3xl` 留给文章与文档标题），按变体切换的写 `text-(length:--cc-*)`；字重 `font-normal / medium / semibold / bold`。Tailwind 自带的数字档（`p-3`、`h-8`、`max-w-md`）、`!` 强制、`sm: / lg:` 断点都不是标准。变量写法只能引用 `--cc-*`，组件不直接用 `--code-* / --ccs-*`。同一组外观出现两处以上，收成一个 `cc-*` 类。
- 新增档位：先在 globals.css 登记并注明出处 → 需要时接到 `@theme` → 组件使用；不许在组件里用 `!` 或覆盖类压标准类，缺档位就补档位或补变体。`lib/utils.ts` 的 `cn()` 要同步登记新的字号、字重名（tailwind-merge 不认识会当成颜色吞掉别的类）。
- 断点只有一条 768：`hooks/use-mobile.ts` 的 `MOBILE_QUERY`、`@theme` 的 `--breakpoint-md`（`max-md: / md:`）、globals.css 的 `@media` 都是 `(width < 768px)` / `(width >= 768px)` 这一段文本（测试核对）。
- 触屏的三种判定各管各的：`data-cc-touch`（`lib/variant.ts` 写：手机变体，或粗指针且不是桌面窗口）管尺寸与点按区，globals.css 用 `:root[data-cc-touch]`、组件用 `in-data-[cc-touch]:`；`data-cc-variant` 管外壳变体；`lib/touch.ts` 的 `touchOnly()`（`hover: none`）只管「没有悬停」时的行为（长按、操作常显），不管尺寸。
- 只做舒适档，不加紧凑档。
- JS 里用到的值（菜单离触发器、子菜单离父菜单、离屏幕边，提示框延时，拖动浮层层级，列表预取距离）同样只在 globals.css 定值，**用的时候读**（打开菜单、提示框出现、拿起拖动那一刻），不在导入或首次渲染时读（生产页动态插入样式表，太早读到空串）：`lib/css-var.ts` 的 `fixedPx / fixedMs`（不随变体变的值，读到就记住）与 `cssVar / cssPx / cssMs`（现读现算），各处的回退常量与 globals.css 同值、测试核对。菜单离触发器写 `sideOffset={menuOffset()}`（`components/ui/menu.tsx`），不写数。
- 菜单（B3 起）：主菜单离触发器一律 `menuOffset()`，子菜单 `SubMenu` 不写 `sideOffset`（用包装的默认 `--cc-menu-sub-offset`）；最小宽用宽档 `min-w-(--cc-menu-w-sm / -md / -lg)`，不用 `!min-w-*` 压 `.cc-menu`；菜单里不能点的说明行（如用户菜单顶上的账号）`.cc-menu-label`；快捷键提示只经 `<Shortcut>`：菜单项右边是 `.cc-menu-key`（与会话行菜单的单字母同一类），设置里那一格是 `.cc-kbd`。
- 组件库（`components/ui`）只留在用的部件，外观同样只走变量与 `cc-*` 类；提示框、弹窗的进出场用 `.cc-pop` / `.cc-overlay`，骨架用 `animate-cc-pulse`，不用 tw-animate。
- 开着不操作不耗电（10-18 起，[静置动效记录](../work/2026-10-18-cc-idle-animations.md)）：装饰动画（产品形象小单的眨眼、笑、尾巴、铃铛、举牌、打勾）不无限循环，出现时播 `--cc-mascot-*-runs` 轮（轮数 × 时长不超过约 10 秒）后停在静止姿势——keyframes 的结尾就是不加动画时的样子；页面在后台（`visibilityState` hidden）时暂停、回到前台接着播；问候语换了且上一次已播完才再播一次。无限循环只留给「正在工作」的动画（按钮与行内转圈、等待回复的四点、在跑的对话行尾脉冲点、骨架、加载页形象上下浮），而且只在工作时挂载；减少动效时照旧不播（转圈除外）。动画轮数同时长一样只写 `var()`（闸门：globals.css 记 R13、任意属性记 R8、内联 style 记 R12）。`tests/test_cc_idle_motion.py` 核对：装饰动画轮数与总时长、静止姿势与 keyframes 首尾一致、infinite 白名单、组件里「在忙」动画与 `setInterval`、自排 `requestAnimationFrame` 的循环都登记了只在什么时候跑。

## 2. 对话里的卡片

照官方 inline card 规矩（允许卡片、文件卡片、提醒卡片、步骤详情都适用）：

- 按钮最多 2 个，放在卡片底部；
- 数据最多 4–5 项，多了列前几项 + 「还有 N 项」；
- 高度随内容，**卡内不套滚动**；长内容先收起，点一下整段展开；
- 加载用骨架（`Skeleton`），不用转圈；
- 手机点按区不小于 44pt：能点的元素加 `cc-tap`（触屏时最小 44×44）；共用控件的手机档写在 globals.css 的一条 `:root[data-cc-touch]`（手机变体，或粗指针且不是桌面窗口；B1 前是手机变体与粗指针两份，CC 桌面版在触屏上也不改电脑值）：菜单项、文字按钮、输入框、图标按钮、列表页小按钮、搜索框、会话栏卡头与小按钮、抽屉组操作都是 44（第 11 轮按钮排查，前后数值见 `docs/work/2026-10-16-cc-shell-design.md` 3.18）；看起来要小的（开关、附件 ×、筛选 ×、问候下的胶囊）外观不变，用伪元素把点按区补到 44；
- 不在卡内放菜单 / 弹出层，用看得见的按钮；颜色、圆角只用变量。
- 卡片按钮照 Claude 的问题卡片（用户 10-17）：统一用 `.cc-card-btn`，底部靠右，次要在左（浅灰实心、无边框：叠色 2，悬停叠色 3）、主要在右（`data-primary`，反色实心）；不带图标（只有图标的「×」是圆钮 `data-icon`）；电脑 24 高（设计系统 xs 档，6.webp「跳过 / 提交」约 24；第 11 轮前 28）、左右 8、13px、字重 500，手机大号全圆角胶囊 44（8.webp「提交」约 42–43，第 11 轮前 48）、16px；手机浮层右下「允许」至少 112 宽、浮层圆角 24。允许卡、提醒卡「打开通知」、欢迎页安装提示、记住的选择、知识库编辑「取消 / 保存」、待处理「确认 / 立即推送 / 交给 CC」同用这一类。
- 允许 / 拒绝卡：问话正文色、常规字重（不是按钮，没有悬停底与箭头），卡里只一层框，改动清单细线隔开；点完卡片留在对话流原处（不收进步骤组），问话加粗，按钮行换成一行淡色的所选那一项（以服务端结果为准；确认还没送到时再淡一档），这一步没做成时加危险色「失败」；没答就没了（中断、回合结束）写淡色「已取消」——当场、切回、重开对话、别的设备上答过都是这一个样子；确认没送到时按钮还在，上方一行原因（顶上不另报连接中断）。手机上等回答的那张只浮在输入框上方（大圆角弹层、粗体问话、右上圆形「×」＝拒绝、右下大号「允许」），一次一张，对话流里不画；点完的卡片问话上方多一行小号淡色「问题」。

## 3. 已收集的 Claude 交互

- **侧栏**：顶部「新建」；「已置顶」一组在最上；对话按项目 / 公司分组，组头点击折叠（折叠状态记在本机，搜索时全展开，手机上不折叠），组头旁「+」在该组里新开；每行悬停出「…」行菜单，手机长按弹同一菜单（不触发系统选字）；跑着的行尾转圈、跑完没看过一个蓝点。
- **工作状态行**（`thread.tsx` 的 RunIndicator）：按下发送即出现在用户消息下；橘色小点 + 本地每秒走的计时；在跑某一步时说进行时（「正在读取 xxx」）；电脑上等用户点「允许」时换成橙色小点 +「需要您的输入」（浅橙底、品牌橙字的小胶囊，在卡片下方；手机上由浮层代替，不显示），点完换回来；在跑却 30 秒收不到服务端任何消息时改说「连接中断，正在重连」；直到这一轮真正结束才消失。
- **跟随滚动**（照 Claude 手机截图，用户 10-17；三端一样）：不跟随。发出后这一轮顶到上面、下面留空（组件库 `turnAnchor="top"`），手机上同时收起键盘；回复在出时画面停在这一轮开头，下面有内容就在输入框上方居中出 ↓（40 的圆），点了平滑滚到最新。打开一段对话（`lib/follow-scroll.ts`）：已结束的停在最新一条；正在回复的停在这一轮开头、提问顶在上面（与发出后同一落点），上面的内容后来变高（量出真实高度、结果文件卡、缩略图）也对回去，用户一碰就交还（在输入框里点、打字、发送不算），之后不跟随、出 ↓，这一轮跑完也不改去最底下；在不在跑按会话状态（离开再回来以核对后的为准），不看组件库的标记；本页刚发出（新对话第一条）不算打开，位置交给组件库。
- **输入法回车**（`lib/ime.ts`）：选字中的回车（`isComposing`，以及 Safari 结束选字时的 keyCode 229）不发送。

## 4. 参考边界

- 取值一律以官方文档为准；社区逆向仓库（如 github.com/Caho1/open-claude-ui）只当行为参考，不拷代码、不引组件、不加依赖。
- 视觉改动要在浅色 / 深色 × 电脑 / 手机各看一遍再交付。
