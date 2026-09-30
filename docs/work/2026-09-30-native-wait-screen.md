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

## 按页的进度（用户 2026-09-30：「录入工作台按页分进度，不要按份，ERP 那边一样」）

- 共用 `p.waiting(..., {pages})`：每 1.5 秒问一次服务端读了几页，多一行「已识别 N/总 页 · 百分比」（`shell.wait.pages` 四语）；
  问不到或还没开始读就不显示这一行。
- Cowork 录入：先把这一批全部上传（最多 6 份同时；上传后服务端数出每份几页），再最多 6 份同时识别。识别任务的
  `detail` 多回一个 `pages_done`：引擎按页顺序读，每次付费调用前记下「读到第 N 页」＝前 N−1 页已读完（`store.pages_reached`，
  存缓存、只作展示）；已存 / 不是发票按全部页数。等候页显示「已识别 N/总 页 · 百分比」，总页数是这一批页数之和。
- ERP 采购 / 销售：网页识别仍是一次同步调用；发起时带随机进度号，`policy.read_all` 逐页读，每读完一页写
  `recognition.progress`（按当前用户隔离、缓存 1 小时），新接口 `api_recognise_progress` 回 `{done, total}`。
  多张图片 / 单图一起读的，读完一起算。LINE 路径不带进度号，不变。
- 验证：`tests/test_cowork_intake_pages.py` 3 条、ERP `test_line_intake` 81 条（新增逐页回报、按用户隔离）、Cowork 离线测试
  （除两条缺 `docx` 的导入错误）、原生 Desk 契约、架构、前端 i18n 通过。页面效果未看：压测全量回归期间不重载测试机；ERP 按用户口径
  等 Cowork 闭环后一起上。改到共用层，合入前在生产镜像跑候选离线检查。
