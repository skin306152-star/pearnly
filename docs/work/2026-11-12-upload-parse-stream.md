# 上传边收边写盘 · 第 1 步：网页入口经 `pearnly_core.web.wsgi`（ADR-028）

分支 `pearnly/upload-parse-stream`，基线 origin/main 6275e4999。主会话 11-12 审定评估后批准（共享笔记「C线-上传边收边写盘-评估-待审-11-12」，
含线上 11-12 内存回读的解释与只读命令）。第 2 步（各上传接口不再把文件读成 bytes）另一支。

## 改了什么

- 新增 `apps/pearnly_core/pearnly_core/web/`：`multipart.py`（只换 multipart 一路的 `make_form_dict`，钉 Frappe 16.33.0 源码指纹）、
  `wsgi.py`（进程入口：装好替换再导出 Frappe 的 `application`；`python -m pearnly_core.web.wsgi <端口>` 同 `bench serve --noreload`）。
- 运行定义：生产 compose 五处 gunicorn 应用改为 `${PEARNLY_WSGI_APP:-pearnly_core.web.wsgi:application}`；回归实验室、场景预检、
  本机开发（01-bench）与三份预览编排同用新入口。
- 测试：`tests/test_web_multipart.py`（候选离线车道，生产镜像里跑：逐项与 Frappe 原实现比对、非 multipart 原样交回、40MB 不进内存、指纹）；
  `tests/test_web_entry.py`（Development checks：每处运行定义都经新入口、回退一行、候选车道已登记）。
- ADR-028：为什么不能用钩子、改了哪一段、Frappe 升级怎么办、回退。

## 回退（同 ADR-028）

生产 `/opt/pearnly/secrets/runtime.env` 加 `PEARNLY_WSGI_APP=frappe.app:application`，按发布流程重新激活四个产品；
读回 `docker inspect -f '{{json .Config.Cmd}}' <网页容器>` 末尾是 `frappe.app:application`。部署记录里照抄这一段。

## 验证（11-12，被测 0602842ca）

- 先红后绿：`tests/test_web_multipart.py`（生产镜像里跑，8 条）、`tests/test_web_entry.py`（CI，4 条）在旧代码上都红（没有新入口）。
- 本地闸门 224 步过；候选离线 109 组 LANE EXIT 0（生产镜像 5078ed47，含新的对比测试）；分支 Development checks 绿。
- **构建镜像**：`build-production` 对本分支手动触发（运行 43151670599），构建内的候选车道全过，镜像
  `ghcr.io/skin306152-star/pearnly/frappe@sha256:f546a2afd01324ae716587b732949c56a4f8fbc683b9afa6a87545065c38c318`。
- **实验栈用这个镜像跑流程全套**（`pearnly-cline-lab`，网页进程经新入口，jobs 3）：108 条 361 格，首轮 359/361，两格「page steps timed out after 900s」（`erp.line.card-lines` 旧数据 / 电脑、`cc.operator.replay` 旧数据 / 电脑，无断言失败，同已知的满载偶发）；`--rerun-failed` 单格 16.1 秒 / 21.3 秒通过，合并后 **361/361**，GitHub 状态 `pearnly/flows` 已回写到 0602842ca。
- **内存实测**（实验栈里另起一个网页进程，原入口 `frappe.utils.bench_helper frappe serve` 与新入口 `python -m pearnly_core.web.wsgi` 各一次，
  同款 39MB 合成 PDF 传 CC 附件接口，量进程匿名内存峰值增量；实验室替身智能体内存放宽到 3g、实验栈网页容器放宽到 6g，只为装得下原入口 15 份）：

  | | 1 份 | 15 份同时 |
  |---|---|---|
  | 原入口（Frappe 原样） | +192MB | +2968MB（每份约 198MB），15/15 成功 |
  | 新入口（第 1 步） | +40MB | +617MB（每份约 41MB），15/15 成功 |

  新入口剩下的每份约 40MB 是接口自己把文件读成 bytes 那一份，第 2 步去掉。
- **浏览器逐产品点上传**（内置浏览器，实验栈新入口，页面自己的上传函数或浏览器 FormData，1.2–1.7MB 文件、超过 werkzeug 500KB 落盘阈值）：

  | 入口 | 怎么点 | 结果 |
  |---|---|---|
  | Desk 原生附件（`upload_file`） | ERP 站点页面里 FormData 交 `/api/method/upload_file`（私有、Home/Attachments） | 通过：200，读回大小一致 |
  | ERP 采购 / 销售识别原件、入库单附件 | 采购页 `frappe.pearnly.erp_upload_original(file, purchase / sales / stock_in)`（实验栈没配原件层，走经服务器的 multipart） | 通过：三处都 200，File 读回大小一致 |
  | Cowork 录入 | 录入工作台 `frappe.pearnly.upload_original` | 通过：来源文件建成，大小、页数、sha256 对 |
  | CC 附件 | `/assistant` 页面里建对话、FormData 交 `pearnly_core.assistant.api.upload`，再按文件编号下载 | 通过：200，下载回来逐字节一致 |
  | 充值凭证 | `pearnly_core.payments.proof.upload` | 通过：200，读回一致 |
  | 控制面 | 实验栈控制面站点（Administrator）Desk 原生 `upload_file` | 通过：200，读回一致 |
  | fatcc.com（control.assistant） | — | 没测到：实验栈没有这个站点；它的 CC 附件与产品站点同一个接口，已由 CC 附件那一行覆盖 |

  页面控制台只有实验栈固有的报错（没有实时推送 socket.io、未登录前的 403 / 404）和我先用一份手写的坏 PDF 试出来的 3 个 500（PDF 读不出，与本改动无关）。
