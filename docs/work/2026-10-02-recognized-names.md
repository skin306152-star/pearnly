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
| 人在复核页改正过某税号（13 位且校验位对）的名称 → 按税号记住；以后入库读出的**相近写法**（相似度 ≥ 0.8）换成人定的；同一原件里没人改过、没推过的草稿相近写法跟着改（不算人改过） | `history/names.learn` / `prepare` / `renamed`、`history/storage.update_pages` |
| 只有数字、只有符号、或只是一个泰文计量单位的「品名」→ 清空（不编） | `contracts/recognized_names.placeholder_item_name` |
| 空品名 → 按「卖方税号 + 单价」补人补过、且只补过一种的名称；补不出就留空 | `history/names.prepare` |

- 记忆是新的私有 DocType `Cowork Name Memory`（原生接口一律拒绝，同 `Cowork Supplier Category Memory`）。品项里没有「单位」，所以键是卖方税号 + 单价。
- 模型原读法仍在记录的 `ai_raw`。分类记忆按卖方名称取，所以名称处理放在分类之前。
- 不用主数据（工作空间 / 客户名册）的名称直接替换：那里的 `display_name` 常是简称（testcowork 本方就叫「testcowork」）。
- 「品名看不清不许编」的提示词规则由修复窗口合进它的读法批次（`codex/invoice-read-rules` `df81e3e4`），在金标上前后对比；本分支不碰 `ocr/engine/prompts.py`。

### 第一版被金标否掉的做法（bb155b18）

第一版在一批里按「同一税号取出现最多的写法」自动统一、并清空纯数字 / 符号 / 中英文单位词的品名。拿 testcowork 上
eval1 的原样识别结果（270 张票、861 行品名，只读、结果未带出生产机）套上去：

- 9 处改名里只有 2–3 处是真读错（กิซเช่/กิซเซ่、เอเชีย/เอเซีย）；其余是同一税号下读出了不同单位（海关 vs 报关行、
  两家不同买方）或泰文名 vs 英文名（自来水局被改成英文名）——那是税号读错或套用，统一反而把错藏起来。
  相似度也分不开（CPF vs CKH 0.91，Big C 各写法 0.83–0.96），所以取消一批内自动统一，只在人改正后复用，且只换相近写法。
- 清掉的 5 个品名里，运费单上的「PCS」是一项收费、只印货号的收据「740462-0010」是有效信息，所以只清纯数字和泰文单位词。

## 验证

- 金标 eval1（270 张票）套新规则：没有人改正时名称 0 处改动、品名 0/861 被清空——对金标零影响。同一批里多写法的 9 组中
  3 组是相近写法（人改正一张后会跟着改），6 组差得远（不动）。
- `tests/test_cowork_recognized_names.py`（产品镜像内，用 Big C 27 页原样识别结果与上面的金标反例）：第 1、2、3、5、11、24 页的
  编号 / 单位 / 邮编品名被清空；人改正一次后 9 种写法都换成人定的；海关 / 报关行、泰文 / 英文名不换；同价两种品名不补；输入不被改动。
- 回归：document_manifest、review_reasons、category_memory、history_contract、line_intake、intake_preview、express_preflight、
  intake_pages、ocr_tasks、remote_ocr、direct_upload、intake_live、remote_forward 等在镜像内通过；`check-architecture`、native desk 检查通过。
- 两个用例加进 `scripts/ci-candidate-checks.sh` 的离线车道。
- 还缺：真实 Site 上的 DocType 安装 / migrate 与复核页点验——修复窗口在下一批统一上线时于测试租户上 migrate、点验后回报。

## 交付

不自己部署：推分支、CI 过后把 SHA 交修复窗口统一排批上线。
