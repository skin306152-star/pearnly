# CC 手机页能整页左滑走（11-28 iPhone Chrome）

分支 `pearnly/cc-mobile-no-hscroll`，基线 `origin/main` 568f4b7f6（= 线上）。缺陷修复，不改入口、文案、权限。

## 现象

用户 11-28 11:22 在 iPhone Chrome 打开线上 testerp 的 CC 首页（深色、已登录）：页面能向左滑走，右边一大片空白，底部有横向滚动条，
问候语只剩「HAO」，「更多」和输入框被推出左边缘；左上角菜单按钮没动。截图 `~/pearnly-devtools/handoff-1128/cc-mobile-overflow/user-iphone-1122.webp`。

## 复现（只读，线上 testerp / testcowork，e2eerp / e2ecowork 自己登录，无头 Chrome 手机模拟）

本机已有 4 套以上别的窗口的实验栈在跑（超过三套上限），没再起新栈，按任务书兜底在线上只开页面看。
375 / 390 / 430 × 深浅两色 × 新对话首页：`document.documentElement.scrollWidth` 等于视口宽，但对话区
`[data-slot="aui_thread-viewport"]`（`overflow-x-auto`）的 scrollWidth = 1062（ERP）/ 970（Cowork），clientWidth 只有 373–428；
把它的 scrollLeft 拉到底，整屏空白、只剩左上角菜单按钮，和用户截图一致。

**是真的横向溢出，不是 iOS 橡皮筋回弹**：回弹松手即回、不出横向滚动条；这里是对话区这个内层滚动容器真的多出 600 多 px 可滚范围，
菜单按钮在它外面所以不动，滚动条也是它的。

撑宽的元素：业务菜单（采购 / 销售 / 收款 / 盘点 / 更多）用来量每颗按钮宽度的隐藏一排 `.cc-business-measure`。它绝对定位、
`visibility: hidden`，但看不见的绝对定位元素照样算进祖先滚动容器的可滚动范围；整排摆开约 1046px。
引入提交 b62b4986d（11-17「业务菜单按定稿做成描边小按钮」加了这排测量层）；对话区的 `overflow-x-auto` 是 10-10 的 36b23f0e2，两者叠加才出问题。
「对话中」画面：本对话选了业务时只留一颗按钮、不出测量层，不溢出；没选业务时同首页。DMS 没声明 CC 业务（`pearnly_guides`），不出这排菜单。

## 修法（根因，不在外层盖 overflow hidden）

- `globals.css`：`.cc-business-measure` 自身 `width: 0; height: 0; overflow: hidden`，子按钮 `flex: none` 保持自然宽度——量出的宽度不变，
  不再给对话区贡献可滚范围。线上注入同样样式实测：对话区可滚宽度回到自身宽度，各宽度摆出的按钮（375/390「采购 销售 更多」、430 多一颗「收款」）
  与量出的每颗宽度（84 / 84 / … / 106）修前修后完全相同。
- 通用断言（`scripts/regression/ui_steps.mjs` + `scripts/flows/run.py`）：手机宽度、页面在 `/assistant` 时，每一步量根节点和占半屏以上、
  能横滚的滚动区（回复里的表格、代码块与窄的标签条不算），可滚宽度大于自身宽度就记下，汇总成检查「no horizontal scroll on the CC phone page」。
  这段判定脚本在线上实测：修前报「aui_thread-viewport 1062 > 373」，注入修法后为空。
- 单测 `tests/test_cc_guide_menu.py` 守住测量层这条样式。

## 第二个根因（新断言在 GitHub Flows 抓到，11-28）

修掉测量层后，新加的断言在 Flows 里又报：`cc.chat` 手机（回复中、长回复、回到一半停止之后）对话区 434 > 388，`cc.attach-delete` / `cowork.cc.attach-delete` 删除对话时 451 > 388。
断言先报「伸出最远的元素」时指到了测量层里被裁掉的那颗「更多」（诊断没排除被裁剪的祖先，已改为排除并报最多三个真正伸出去的元素）。
线上用 e2ecowork 依次打开 7 个对话、注入测量层修复后仍有两个是 518 > 388：**看得见的那一排**摆了 4 颗加「更多」（约 502px）而这一排只有 356px。
原因：`useMenuFit` 只在业务清单变化时重量；清单变化那一刻这一排正好没摆出来（回复中、录音中、已选中一项时 `BusinessMenu` 不摆这一排，引用为空、量不到就跳过），之后这一排再出现沿用没量过的初始值「前 4 项」。
修：把「这一排此刻摆没摆出来」（`shown`）也作为重量条件（`useMenuFit` 依赖 `[key, first, shown]`）。
验证：本分支现编前端在线上（浏览器里把 CC 页面的打包文件换成本分支构建）依次打开同样 7 个对话，全部 388 = 388；修前同一顺序 518 / 1458。

## 截图（`~/pearnly-devtools/handoff-1128/cc-mobile-overflow/`）

`修前-手机390-深色-左滑后.png`（整屏空白，同用户）、`修后-手机390-深色-左滑后.png`（滑不动，画面正常）；probe/ 里有全部宽度与两色。

## 没测到

- 真 iPhone（我们驱动不了）：用无头 Chrome 手机模拟（iPhone UA、触屏、375/390/430）代替，横向溢出是布局层面的，与浏览器内核无关；
  iOS Safari/Chrome 的回弹手感没法模拟。
- 修后的真实构建在线上未看（修后结论来自线上页面注入同样样式）；本分支的流程在 GitHub Flows 上跑。
