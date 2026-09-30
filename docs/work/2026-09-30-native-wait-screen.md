# 识别等候改成白底 + 官方转圈（Cowork 录入、ERP 采购 / 销售，2026-09-30）

用户：「三个 app 涉及等待的全部用官方原生」；看过预览后定「白屏加动画吧，这样更好」。

- 分支 `codex/native-wait-screen`，基线 main `c97eac21`。改到共用层 `pearnly_core`（外壳），合入前要在生产镜像跑候选离线检查。
- 共用组件 `p.waiting(page, text, {elapsed})`（`pearnly_core/public/desk/product.js` + `product.css`）：盖住这一页内容区的白底，
  居中是框架样式表自带的 `spinner-border`，下面一行真实进度；顶栏、侧栏照常；页头按钮等候期间停用、结束按原样恢复。
  `elapsed` 时多一行「已用 N 秒」（`shell.wait.elapsed` 四语）。
- Cowork 录入：原来整屏灰遮罩（`frappe.dom.freeze`）写「已完成 / 总数 · 百分比」→ 同样的文字放在白底上。其余短操作的遮罩不动。
- ERP 采购 / 销售：原来整屏灰遮罩「Recognizing...」、没有进度 → 白底 + 转圈 + 「Recognizing...」+ 已用秒数（一次调用识别，
  服务端中途不回报进度，只写真实的已用时间，不做百分比）。
- 验证：架构、原生 Desk 契约、前端各节点测试通过；页面效果待测试机（Cowork 录入在 Cowork 栈、采购 / 销售要切 ERP 栈）。
- LINE 不动。未合并、未部署。
