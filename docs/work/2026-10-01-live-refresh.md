# 页面刷新推送为主、完成后通知、少量上传即出结果（2026-10-01）

压测窗口转达用户："把刚讨论的都做一下吧，只要是给产品带来优势的交给修复窗口一起排进去"。分支 `codex/live-refresh`，基线 main `2821e5c1`。
预览预存、Cloud Tasks、Workflows 由「Cowork 识别派发与预览提前生成」窗口做，本分支不碰转发层与 `files.api.page`。

## 1. 列表刷新：推送为主，定时兜底（做）

- 原来：对账列表不管有没有任务每 5 秒问 `recon_center.api.active`（300 人停在列表页约每秒 60 个请求）；进行中页每 3 秒；
  录入列表有「录入中」就每 4 秒重拉；首页固定 30 秒。
- 共用 `p.live`（`pearnly_core/public/desk/product.js`）：收到服务端实时事件就刷（同一阵子的合成一次）；兜底定时——本页有自己在跑的
  任务时短间隔（5 秒，进行中页 3 秒），否则 60 秒；页面看不见不刷，重新看得见 / 窗口拿回焦点马上刷一次；切走或 `stop()` 后摘掉监听。
- 用到的事件：
  - `cowork_recon_finished`（已有，推给发起人）→ 对账列表、进行中页、首页；
  - `pearnly_ocr_work`（已有，识别有结论推给提交人；录入票存好时 `kind=cowork.invoice`）→ 录入列表、首页；
  - `cowork_intake_changed`（新）：推送状态写回时（`history/push_state.mirror` / `record_failure`）推给这张票的上传人与这次推送的
    发起人，事务提交后才发、只带票号。
- 别人的改动（同事推的、同事传的）没有推给本人：最晚 60 秒兜底刷到。不广播给全站：一张票一变全站几百个列表同时重拉，
  比原来的轮询更重。LINE 进来的单：识别完成推给绑定的那个用户（`pearnly_ocr_work`）。
- ERP / DMS：只有共用首页这一处固定轮询，同样改成 `p.live`（识别结束会扣费、出明细）。LINE 绑定弹窗里等扫码的 4 秒轮询只在
  弹窗开着的一两分钟里，不改。

## 2. 识别 / 对账完成发 LINE 通知（不做）

用户 2026-10-01：不做（主动推送按条计费、免费额度很小，对产品没有带来优势还增加成本）。

## 同批：撤回 Desk 脚本打包（修收银台）

2821e5c1 上线的「每个产品 Desk 脚本打成一个包」把 testerp 收银台弄坏了（扫码库 onScan 是 UMD，打包后不再挂全局，`ItemSelector`
初始化报错）。改回逐个加载（hooks、`pearnly_core/desk/assets.py`、`frappe.Dockerfile` 回到 12ab7a05 的写法），删掉
`scripts/build-desk-bundles.py`；`tests/test_desk_first_load.py::NoBundleTest` 钉住不再打包。详见 `2026-10-01-desk-first-load.md`。
用户定"下次有东西部署时一起搞"，所以随本分支一起部署，不单独回退。

## 3. 少量上传马上出结果（已具备，不改）

录入上传页本来就是：上传后页面显示「识别中」与按页进度，识别有结论服务端推 `pearnly_ocr_work`，页面立刻取结果并把第一张展开到左边供复核
（`intake-upload.js` `run_batch` → `open_record(records[0])`），不整页刷新。

## 验证

- `tests/test-desk-live.cjs`（Development checks）：事件合并、过滤、短 / 长间隔、看不见不刷、回到页面刷、切走后摘掉监听、出错不影响下一次。
- `tests/test_cowork_intake_live.py`（候选离线道）：推送状态变了推给上传人与发起人、没变不推、记失败也推、推不出去不影响推送。
- 还缺：测试机浏览器实测、生产镜像候选检查（改了 pearnly_core）、部署后压测窗口看请求量。
