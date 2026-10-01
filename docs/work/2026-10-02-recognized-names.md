# 识别入库：买卖方名称按税号统一、品名不编（2026-10-02）

协调窗口（接手 Cowork 三个对账功能压测）分派的第 1 项。分支 `codex/seller-name-item-unreadable`，基线 `10a4f07d`。

## 起因

testcowork 上 Big C 27 页销项扫描件（batch `577624e5…`，原件 `gs://pearnly-cowork-originals-sg/testcowork.pearnly.com/originals/CWS-10bb4b43…pdf`），逐页对过原图：

- 单号、金额、卖方税号都对；同一卖方税号 `0105546015062` 却读出 9 种写法（ไอซ์ 读成 ไลซ์ / ไลจ์、漏 ร、ซ/ช 混读）。
- 品名被收货章盖住时，模型拿品项编号「4」、单位「ถุง」、章里的邮编「10500」当品名，或编出 น้ำมันก๊าด（煤油）、น้ำแข็งแห้ง（干冰）。
  正确的都是 น้ำแข็งแพ็ค。

## 口径（用户 2026-10-02 经协调窗口转达）

录入口不新增识别后的提示或让人选的文案，只提升识别质量、把结果显示出来。所以这里只改值：

| 规则 | 落点 |
| --- | --- |
| 同一税号（13 位且校验位对）一批里写法不一致 → 取出现最多的（一样多取先出现的）；标点空格不同算同一种 | `contracts/recognized_names.plan_batch`、入库 `history/recognition.persist_batch` |
| 人在复核页改过名称 → 按税号记住，以后入库直接用；同一原件里没人改过、没推过的草稿跟着改（不算人改过） | `history/names.learn` / `renamed`、`history/storage.update_pages` |
| 只有数字 / 符号、或只是一个计量单位的「品名」→ 清空（不编） | `contracts/recognized_names.placeholder_item_name` |
| 空品名 → 按「卖方税号 + 单价」补人补过、且只补过一种的名称；补不出就留空 | `history/names.prepare` |

- 记忆是新的私有 DocType `Cowork Name Memory`（原生接口一律拒绝，同 `Cowork Supplier Category Memory`）。品项里没有「单位」，所以键是卖方税号 + 单价。
- 模型原读法仍在记录的 `ai_raw`。分类记忆按卖方名称取，所以名称统一放在分类之前。
- 没有用主数据（工作空间 / 客户名册）的名称直接替换：那里的 `display_name` 常是简称或昵称（testcowork 本方就叫「testcowork」），换上去比读错还糟。
- 「品名看不清不许编」的提示词规则由修复窗口合进它的读法批次（`codex/invoice-read-rules` `df81e3e4`），在金标上前后对比；本分支不碰 `ocr/engine/prompts.py`。

## 验证

- `tests/test_cowork_recognized_names.py`（产品镜像内跑，用 Big C 27 页原样识别结果）：一批统一成一个卖方名称；第 1、2、3、5、11、24 页的编号 / 单位 / 邮编品名被清空；人改过的名称以后直接用；同价两种品名不补；输入不被改动。
- 回归：`test_cowork_document_manifest`（这里抓到并修了一个变量遮蔽）、review_reasons、category_memory、history_contract、intake_preview、express_preflight、intake_pages、ocr_tasks 等在镜像内通过；`check-architecture`、`i18n-build --check`、native desk 检查通过。
- 两个用例加进 `scripts/ci-candidate-checks.sh` 的离线车道。
- 还缺：真实 Site 上的 DocType 安装 / migrate 与复核页点验（testcowork 目前由修复窗口占用做评测，用前先问协调窗口）；金标上的后处理前后对比（打分脚本向修复窗口要）。

## 交付

不自己部署：推分支、CI 过后把 SHA 交修复窗口统一排批上线。
