# CC 两处中文显示修复（2026-10-19）

分支 `pearnly/cc-cjk-fixes`，基线 `pearnly/batch-1019b` f4d7cc58。来源：用户 10-19 在 Windows 测试机 CC 桌面版的截图。

## 1. 定时任务删除确认框是英文

- 原因：`scheduled-tasks.tsx` 的 `tr("Delete {0} item permanently?", 1)` 取的是 `pearnly_cowork.folder_intake.api.terms()` 给出的
  `frappe._(key)`；`TERM_WORDS` 列了 `Delete {0} item(s) permanently?` 两条，但 Cowork 术语表没有，Frappe 16 自带译文里也没有中 / 泰 / 日，
  原样回英文。
- 按类核对：接口列给前端的 213 个词条、页面 `tr()` 用到的 116 个键（含条件分支和按状态 / 菜单 / 默认值拼出的键）逐个查 Cowork 站点
  两个 App（pearnly_core、pearnly_cowork）的翻译包，只缺这两条。
- 改法：Cowork 术语表补两行（中「确定要永久删除 {0} 项吗？」、泰「ต้องการลบ {0} รายการถาวรใช่หรือไม่」、日「{0} 件を完全に削除しますか？」），
  重新生成翻译包。新测试 `tests/test_cc_scheduled_task_terms.py`（已进 CI）：列给前端的每个词条四语齐全、占位符一致；页面用到的键都在接口列出的范围里。
  改前跑测试报这两条缺中文。

## 2. 弹窗标题全角「？」又窄又贴字

- 原因（Mac 上核实的部分）：衬线栈 `"Source Serif 4 Variable", ui-serif, Georgia, "Noto Serif Thai", "Songti SC", "Noto Serif SC", serif` 里，
  Source Serif 4 的 6 个子集与 Georgia（fontTools 读 cmap）都不含 CJK 文字和 U+FF00–FFEF 全角标点；Windows 没有 Songti SC / Noto Serif SC /
  Noto Serif Thai，所以中日文每个字都落到最后的系统回退。CC 桌面版用 QtWebEngine（`companion/src/companion/gui_tomo.py`），没有 Chrome
  按文字种类设的默认字体，回退按单个字符挑字体。本机无界面 Chrome 模拟「栈里没有中文字体」：`lang="ja"` 时同一句「删除对话？」被拆到
  Hiragino Mincho 与 Songti SC 两种字体（CDP `CSS.getPlatformFontsForNode`）。
- 推断（未在 Windows 上证实）：测试机上全角问号落到了一个标点做窄的字体（如 Yu Gothic UI / Meiryo UI 一类），和汉字不是同一个字体。
- 改法：`globals.css` 只加 `@font-face "CC CJK Serif"`（四档字重 300 / 400 / 700 / 900，对应 Songti SC 自带的四档），`src` 依次 `local()`
  Songti SC → Noto Serif SC → Microsoft YaHei，`unicode-range` 只含 CJK 区段（部首、符号与标点、假名、注音、CJK 兼容、统一汉字及扩展、
  全角半角形式；不含 U+2000–206F 通用标点和 Georgia 有字形的竖排形式 U+FE10–FE4F），放在 `--cc-font-serif` 最前。对话框 / 抽屉标题、
  问候语、回复正文（`--cc-font-reply` 默认取衬线栈）都走它。Windows 选微软雅黑不选宋体：SimSun 在 12–16px 用内嵌点阵，回复正文会变成点阵字。

## 比对结果（Mac，无界面 Chrome，全矩阵 14 组 995 个场景）

- 样式快照：16813 条差异全部是衬线元素的 `font-family` 字符串变化（前面多了 `"CC CJK Serif"`），外框、尺寸、位置 0 差。
- 像素：984 个场景 0 像素差（含所有中文 / 日文全角标点所在的场景，Mac 上落到的仍是同一套 Songti SC）。有像素差的 11 个，都不在 CJK 标点处：
  - 10 个在窄屏底部输入框那一条渐变（已知的光栅抖动区域，计算样式 0 差）：phone-light chat-rich、chat-rich-bottom；
    phone-dark chat-rich-bottom、font-sans、width-wide；tablet-portrait-light chat-rich-bottom、font-sans、width-wide；
    desktop-narrow-dark chat-rich-bottom、width-wide。
  - 1 个是泰文问候语字形：phone-light th-welcome（泰文不在新字体的范围里，属已知的泰文字形抖动类）。
- 这 11 个比对工具自动复拍 3 轮没判成抖动；空闲时单独复拍按开发窗口要求停掉了，**没有复拍确认**。
- Windows 上的实际效果没看：由测试窗口在测试机上确认。

## 复测点（测试机 Windows，CC 桌面版，界面中文）

1. 侧栏对话「…」→ 删除：标题「删除对话？」问号是正常的全角宽度、和汉字同一字体。
2. 定时任务「…」→ 删除：标题是「确定要永久删除 1 项吗？」；切泰文 / 日文各看一次。
3. 回复正文里的中文、粗体中文、全角标点（，。？！：「」）正常；抽屉标题、问候语里的中文正常。
4. 泰文界面的问候语、回复里的泰文和原来一样。
