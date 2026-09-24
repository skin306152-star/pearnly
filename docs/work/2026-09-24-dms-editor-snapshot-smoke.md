# DMS 快照编辑器本地浏览器冒烟

2026-09-24；属于 `dms-five-minute-snapshots` 工作包。仅本机合成预览，**不是正式 Site、LINE、DMS 或 OCR 验收**；没有生产部署或外部业务写入。

## 被测版本与隔离

- 基线 `57039ca15595641932f053fd34c49d4e45dba964` 加本任务未提交前端改动；沿用已通过 typecheck/build 的锁定 Node 22.14.0 构建，无依赖升级。
- `EditorPage.vue` SHA-256：`60076e68a56f4d51780b2e2298dd92b122497b60af831cd8048249f8e5075689`。
- `dms-editor.ts` SHA-256：`7c8c9d83217aa335ba49b25f6c80c5a348adcee05a2ab011d88b07ef28ff4573`。
- 构建 `index.html` SHA-256：`435e67639d4a0b6c8ab179892aaf8537ddfba5b47308235625f2fccfe235fd9e`。
- `scripts/dms-editor-snapshot-preview.cjs` 只监听 `127.0.0.1` 随机端口，精确 Host 检查、CSP 限制 same-origin、不配置代理或 LIFF；全部客户/票据/CSRF 为合成值，词条读取本产品四语 CSV。
- 实际 Chrome 地址 `http://127.0.0.1:52617/`，路由 `#/dms/editor?draft=<场景>&editor=customer`；订车模式省略 `editor=customer`。

## 实际浏览器结果

| 场景 | 页面与断言 | 结果 |
| --- | --- | --- |
| `fresh` 客户编辑 | 业务显示 `2026/09/24 GMT+7 08:23`，地址显示 `2026/09/23 GMT+7 10:45` 且只有地址提示延迟；两个独立 `status` 元素，截图确认可见 | 通过 |
| `stale` 客户编辑 | 业务提示同步延迟；地址显示上述独立日期及“每天安排完整更新”，不被业务 stale 污染 | 通过 |
| `pending` 首开 | 显示“DMS 资料正在同步…”；没有客户输入框、没有保存按钮，不把未知资料显示为空客户成功 | 通过 |
| `incomplete` 首开 | 显示“DMS 同步未取得完整资料，没有发布不完整结果…”；没有客户输入框或保存按钮 | 通过 |
| `save-failed` | 实际改姓名为 `SYNTHETIC EDIT RETAINED` 并点击保存；合成 API 返回 503/INCOMPLETE，出现错误提示，姓名保留、保存可重试，不出现“草稿已保存” | 通过 |
| `fresh` 订车编辑 | 实际显示订车、付款、附件区；双时间一致；点击泰语后分别显示 `24/09/2569 08:23 GMT+7`、`23/09/2569 10:45 GMT+7` | 通过 |

API 事件回读只包含词条、配置、合成授权、6 次草稿读取和1次故意失败的假保存；没有成功保存模拟、LINE 发送、客户写入或外部请求。最终订车页面 Console 无 error/warn。

首次预览夹具只读 TSV，旧字段词条显示 key；改为完整产品 CSV 后上述场景重新打开验证。这是已修的本地夹具缺项，未改应用行为。

## 复用与收尾

先完成本任务前端构建，再用锁定 Node 执行 `node scripts/dms-editor-snapshot-preview.cjs`，打开打印的本机地址；替换 draft 场景时重新载入文档。脚本不提供真实业务 API，25 分钟自动关闭，也可 Ctrl-C。

本次两个自建预览进程均已正常结束；最终 PID 77793 退出码 0，端口 52617 回读无监听；自建 Chrome 标签 874114712 已关闭。未停止任何共享容器或其他任务进程。脚本 `node --check` 与 `git diff --check` 通过。保留脚本和本记录供当前任务提交及复现；未提交、未推送、未部署。
