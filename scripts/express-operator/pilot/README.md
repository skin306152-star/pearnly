# CC 代录 Express 试点执行器（10-26，开发工具，不进产品代码、不合入 main）

验证「CC 套壳 Claude，真实打开 Express，按单据一张张录入」：成本、速度、准确率、顺不顺、卡在哪。
报告见 `docs/work/2026-10-26-express-operator-pilot.md`。

## 结构

```
本机（Mac）                                   测试机（lenovo-office-lan，skin3 会话）
run_pilot.py ─ engine.py ─ models.py ──API──> Claude（Haiku 5.5 / Opus 5.5）
     │            │
     │            └─ agent_client.py ══ ssh -L 18765 ══> agent_win.py（pythonw 隐藏运行，只听 127.0.0.1）
     │                                                     ├─ 截屏 / 发键 / 剪贴板贴泰文 / 点按（前台必须是 ExpressI）
     │                                                     ├─ 读账套（只读、只许 69EXP\test 和本机副本）
     │                                                     └─ cc_login.py（凭据管理器 → 填 Express 登录框，只回成败）
     └─ ledger.py（花费账本，~/pearnly-devtools/xp-pilot/ledger.jsonl）
```

| 文件 | 作用 |
|---|---|
| `draft.py` | 草稿单（单种、日期、对方码、对方单号、明细、标准答案合计） |
| `corpus.py` | 在测试机上从「录入推送语料」的 Express 标准答案取单、筛掉带折扣 / 合计对不上的，换到试点账套主档上 → 草稿单 |
| `flows.json` | 固定流程：菜单键、工具栏和各格坐标（标了哪些已实录核实）、明细按键顺序、弹窗规则 |
| `engine.py` | 执行器：固定流程 → 每步看 Express 窗口清单认弹窗 → 关键点让模型看截图核对 → 出意外按「Haiku → Opus」接手 → 读回核对 |
| `models.py` | 调 API：画面核对（结构化 JSON）、电脑操作循环（`computer_toolset_20260801`，服务端清旧截图，自动缓存） |
| `compare.py` | 读回核对：对方码、日期、对方单号、税前 / 税 / 含税、每行品码 / 仓 / 数量 / 单价 / 金额、ISVAT |
| `pricing.py` / `ledger.py` | 按 usage 换算美元；硬上限 90 美元；每满 10 美元停下报主会话 |
| `direct_write.py` | 第 ④ 种基准：同一批草稿单用现有 CC 直接写入代码写进**本机副本**，同一把尺子核对 |
| `report.py` | 汇总成报告表格（每张成本、用时、对错、接手、每月推算） |
| `calibrate.py` | 第一次真跑前：Opus 对着空白单据截图找各格坐标，画标记给人确认 |
| `cc_login.py` | 「授权一次、以后自动登录」雏形：用户本人存一次 → 凭据管理器 → 程序填 |
| `fake_express.py` / `selftest.py` | 离线模拟器和自检（不连测试机、不花钱） |
| `start-agent.ps1` / `stop-agent.ps1` / `deploy.sh` | 测试机上起停代理；推代码 |

## 四种方式

| `--mode` | 做法 |
|---|---|
| `haiku_fixed` | ① 固定流程按键；表头、每行、保存前后由 Haiku 5.5 看截图核对；出意外先 Haiku 接手（≤8 轮），不行再 Opus 5.5（≤14 轮）（主推） |
| `opus_fixed` | ② 同上，核对和接手都用 Opus 5.5 |
| `opus_vision` | ③ Opus 5.5 全程看图自己录（≤60 轮；只跑 3–5 张看上限） |
| `direct_write.py` | ④ 现有直接写入（基准，不花钱） |

每张单单独一段对话、提示控制在 9.5 万 token 以内；每次调用的 usage 记账换算美元。

## 用法

```bash
cd scripts/express-operator
python3 -m pilot.selftest                                   # 离线自检
~/pearnly-devtools/xp-pilot/venv/bin/python -m pilot.run_pilot --fake --mode haiku_fixed   # 离线模拟跑一遍
```

真跑（先在占用表登记、主会话安排用户登录服务器版 Express）：

```bash
bash pilot/deploy.sh
ssh lenovo-office-lan "powershell -ExecutionPolicy Bypass -File C:\PearnlyStress\_xp_pilot\pilot\start-agent.ps1 -Pythonw <pythonw 路径>"
~/pearnly-devtools/xp-pilot/venv/bin/python -m pilot.calibrate --type RR       # 每种单一次，人看标注图后改 flows.json
~/pearnly-devtools/xp-pilot/venv/bin/python -m pilot.run_pilot --mode haiku_fixed --drafts drafts.jsonl --limit 4
ssh lenovo-office-lan "powershell -ExecutionPolicy Bypass -File C:\PearnlyStress\_xp_pilot\pilot\stop-agent.ps1"
```

退出码：5 公司没确认（看 `preflight.png`）；10 满 10 美元先报主会话、再带 `--ack`；20 要人处理；30 Express 崩 / -70；40 到上限。
停下后原样再跑，从没做的那张接着录。

## 规矩

- 账套只用 `\\accserver\d$\account\69EXP\test`（บริษัท Pearnly จำกัด）；代理代码里写死白名单，别的路径拒读。
- Express 登录由用户本人；程序不输、不找、不存密码（雏形里的凭据只由用户本人在测试机上存，Claude 拿不到）。
- 看到 -70 先别点：执行器直接停（退出码 30）。
- 截图、账本、结果放 `~/pearnly-devtools/xp-pilot/`，不进仓库（截图含公司数据）。
- 密钥只从本机配置读尾号 6qaa 的那把，不打印、不落盘。
