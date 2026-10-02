# ERP 网页识别改后台 + 轮询（2026-10-02，下一批）

分支 `codex/erp-recognise-background`，基线 main c5092f48。来源：Cloudflare 评估查出、压测窗口转用户口径排进下一批——
`api_recognise_sales` / `api_recognise_purchase` 在网页请求里逐页读（PDF 最多 20 页，每页最多 4 次 Gemini + 1 次 Vision，单次最长 120 秒），
今天就会撞 gunicorn `--timeout=120`，也挡在将来开 Cloudflare 代理（100 秒）前面。按规矩随 Cowork 一起上线。

## 改动

- `recognition/background.py`：请求只做权限检查（`guard.require`）、排任务就返回 `{"queued": 号}`；任务进 `ocr` 队列（ERP 识别进程，
  内存按识别配），`job_id` 按用户 + 号去重，超时 1800 秒；以发起人身份、发起时的语言跑原来的 `recognise`（权限、计费、读法都不变），
  `policy.for_web` 把限流 / 余额 / 不确定转成人话。给人看的失败（不是单据、余额不足、忙、权限）原样转给页面；意外错误记 Error Log，
  页面只给「识别失败，请再试一次」。
- `recognition/progress.py`：进度、结果、失败原因按「用户 + 号」存进不淘汰的状态存储（`pearnly_core.execution.state`，原来是会被挤掉的
  redis-cache）；`read` 发现后台任务已不在（进程重启、超时）却没写结果，按失败回，页面不会一直等。
- 推送优先、轮询兜底（压测窗口：几百人同时识别时不能一两秒问一次，同 Cowork 录入 `intake-upload.js`）：后台每读完一页、读完都
  `publish_realtime("pearnly_erp_recognise")` 只推给发起人；前端 `transactions.js` `p.trade.recognise` 收到页数直接更新等候层，收到结论
  立刻读结果，实时连接在线时 20 秒兜底问一次、不在线 5 秒；30 秒仍问不到任何状态（号丢了）按失败。`sales-form.js` / `purchase-form.js`
  改用它，其余填表逻辑不变。状态 1 小时过期。
- 新词条两条（ERP `messages.tsv`）；「Recognition failed. Please try again.」用共用层已有的。
- `tests/test_erp_recognise_background.py`（进候选离线道，生产镜像跑）：排进 ocr 队列并立即返回、坏号不排、按页进度与结果、人话原样转、
  意外错误记日志只给通用提示、任务没了按失败、只读得到自己的、每页与读完各推一条只给发起人、状态 1 小时过期。

## 验证

- 本地：架构检查、i18n 检查、相关 Development checks 通过；新测试在生产镜像（e25cd943）里 8 项通过。
- 还缺：测试环境里用浏览器点 ERP 采购 / 销售「上传识别」——单页图片、多页 PDF、不是单据的文件三种，看等候层页数、结果填表、失败提示
  （按「改脚本加载方式要逐产品点页」，ERP 随 Cowork 一起验）。真实识别会计费，用测试租户。
