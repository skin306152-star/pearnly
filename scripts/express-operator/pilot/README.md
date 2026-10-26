# CC 代录 Express 试点执行器（10-26，开发工具，不进产品代码、不合入 main）

验证「CC 套壳 Claude，真实打开 Express，按单据一张张录入」：成本、速度、准确率、顺不顺、卡在哪。
报告见 `docs/work/2026-10-26-express-operator-pilot.md`。

**试点电脑**：10-26 起改用录制笔记本 `rec-laptop`（DESKTOP-8BRD5AV，专给试点用），在用户本人的 Windows 账户 aksar 会话里跑
（aksar 已映射 `\\accserver\ACCOUNT`，不用输服务器密码）；工作目录 `C:\XpPilot`。Express 用服务器上 **69EXP** 那套
（试点公司只在它的公司列表里），账套 `\\accserver\ACCOUNT\69EXP\test`。

**实测录法**（细节见 `flows.json` 和报告附录 D、F）：工具栏按命令号发；菜单先单独按 Alt 再按数字；表头按回车走、按标签填；
明细点进表格（grid_click）；单位框按「ตกลง」；价格类型按草稿单选；录到明细后 Express 已占号写账，放弃只能作废；不用 Esc。

**做法（主会话 10-26 定）**：
- 识别走产品本身、单据只看一次：草稿单 = 产品正式识别的结果（`ocr_drafts.py`）；标准答案只用来核对，不当输入。
- 代录分四层，能不截图就不截图：① 键盘固定流程 → ② Windows 系统接口读状态（Win32 / UI Automation，读焦点格、弹窗文字）
  → ③ 读 DBF 核对 → ④ 都判断不了才截图给 AI（Haiku 先、Opus 兜底）。每层记次数，截图给 AI 的张数单独记。
- 不改、不注入、不反编译 Express 程序本身。

## 结构

```
本机（Mac）                                   试点电脑（rec-laptop，acct 会话）
run_pilot.py ─ engine.py ─ models.py ──API──> Claude（Haiku 5.5 / Opus 5.5）
     │            │
     │            └─ agent_client.py ══ ssh -L 18765 ══> agent_win.py（pythonw 隐藏运行，只听 127.0.0.1）
     │                                                     ├─ 发键 / 剪贴板贴泰文 / 点按（前台必须是 ExpressI）
     │                                                     ├─ 读状态不截图：Win32 焦点格与弹窗文字、UIA（pywinauto）
     │                                                     ├─ 截屏（只在要给 AI 看、或留证时）
     │                                                     ├─ 读账套（只读、只许 69EXP\test 和本机副本）
     │                                                     └─ cc_login.py（凭据管理器 → 填 Express 登录框，只回成败）
     └─ ledger.py（花费账本，~/pearnly-devtools/xp-pilot/ledger.jsonl）
```

| 文件 | 作用 |
|---|---|
| `draft.py` | 草稿单（单种、日期、对方码、对方单号、明细、识别花费、只用于核对的标准答案） |
| `ocr_drafts.py` | 产品识别结果（甲骨文上「录入推送语料」抽样 250 份）→ 草稿单；和标准答案对上的带 answer |
| `corpus.py` | （备用）从答案账套原表取多行单，只用来测「行数多了操作要多久」，不算准确率 |
| `probe_ui.py` | 第 2 层实测：原生 Win32、pywinauto win32 后端、uia 后端各能从 Express 画面读到什么（`run_pilot --probe 名字`） |
| `s_stats.py` | 在测试机上只读统计 S 盘副本：每家客户每月单据量和种类（报告主结论的客户分档） |
| `flows.json` | 固定流程（10-26 实测）：工具栏命令号、各单种菜单、表头要填的标签和走到哪格为止、明细表格偏移、底部合计标签、弹窗规则 |
| `engine.py` | 执行器：键盘固定流程 → 每格打完读焦点格核对、每步读窗口清单认弹窗 → 保存后看 DBF 行数 → 读不到才截图给 AI → 出意外按「Haiku → Opus」接手 → 读回核对草稿单和标准答案 |
| `models.py` | 调 API：画面核对（结构化 JSON）、电脑操作循环（`computer_toolset_20260801`，服务端清旧截图，自动缓存） |
| `compare.py` | 读回核对：对方码、日期、对方单号、税前 / 税 / 含税、每行品码 / 仓 / 数量 / 单价 / 金额、ISVAT |
| `pricing.py` / `ledger.py` | 按 usage 换算美元；硬上限 90 美元；每满 10 美元停下报主会话 |
| `direct_write.py` | 第 ④ 种基准：同一批草稿单用现有 CC 直接写入代码写进**本机副本**，同一把尺子核对 |
| `report.py` | 汇总成报告表格（每张成本、用时、对错、接手、每月推算） |
| `calibrate.py` | （备用）Opus 对着空白单据截图找各格坐标；实测后流程不靠坐标，没用上 |
| `console.py` | 手动给代理发一批动作、拉回截图（选公司这类一次性准备步骤） |
| `open_share.py` | 桌面快捷方式用：请 Windows 自己弹服务器凭据框（rec-laptop 改在 aksar 会话后没用上） |
| `cc_login.py` | 「授权一次、以后自动登录」雏形：用户本人存一次 → 凭据管理器 → 程序填 |
| `fake_express.py` / `selftest.py` | 离线模拟器和自检（不连测试机、不花钱） |
| `start-agent.ps1` / `stop-agent.ps1` / `deploy.sh` | 测试机上起停代理；推代码 |

## 四种方式

| `--mode` | 做法 |
|---|---|
| `haiku_fixed` | ① 键盘固定流程 + 不截图读状态 + 读 DBF；判断不了才截图给 Haiku 5.5；出意外先 Haiku 接手（≤8 轮），不行再 Opus 5.5（≤14 轮）（主推） |
| `opus_fixed` | ② 同上，截图核对和接手都用 Opus 5.5 |
| `opus_vision` | ③ Opus 5.5 全程看图自己录（≤60 轮；只跑 3–5 张看上限） |
| `direct_write.py` | ④ 现有直接写入（基准，不花钱） |

每张单单独一段对话、提示控制在 9.5 万 token 以内；每次调用的 usage 记账换算美元。

## 用法

```bash
cd scripts/express-operator
python3 -m pilot.selftest                                   # 离线自检
~/pearnly-devtools/xp-pilot/venv/bin/python -m pilot.run_pilot --fake --mode haiku_fixed   # 离线模拟跑一遍
```

真跑（主会话安排用户在 rec-laptop 上登录服务器版 Express）：

```bash
bash pilot/deploy.sh
ssh rec-laptop "powershell -NoProfile -ExecutionPolicy Bypass -File C:\XpPilot\pilot\start-agent.ps1 -Pythonw C:\Python313\pythonw.exe"
PY=~/pearnly-devtools/xp-pilot/venv/bin/python
$PY -m pilot.run_pilot --mode haiku_fixed --probe empty-RR       # 第 2 层探测：打开单据、新建后各探一次
$PY -m pilot.calibrate --type RR                                 # 只在 to_grid 等必须点坐标的地方需要
$PY -m pilot.ocr_drafts build ~/pearnly-devtools/xp-pilot/corpus/corpus/express masters.json > drafts.jsonl
$PY -m pilot.run_pilot --mode haiku_fixed --drafts drafts.jsonl --limit 4
ssh rec-laptop "powershell -NoProfile -ExecutionPolicy Bypass -File C:\XpPilot\pilot\stop-agent.ps1"
```

退出码：5 公司没确认（看 `preflight.png`）；10 满 10 美元先报主会话、再带 `--ack`；20 要人处理；30 Express 崩 / -70；40 到上限。
停下后原样再跑，从没做的那张接着录。

## 规矩

- 账套只用 `\\accserver\d$\account\69EXP\test`（บริษัท Pearnly จำกัด）；代理代码里写死白名单，别的路径拒读。
- Express 登录由用户本人；程序不输、不找、不存密码（雏形里的凭据只由用户本人在测试机上存，Claude 拿不到）。
- 看到 -70 先别点：执行器直接停（退出码 30）。
- 截图、账本、结果、语料副本放 `~/pearnly-devtools/xp-pilot/`，不进仓库（含公司数据）。
- 密钥只从本机配置读尾号 6qaa 的那把，不打印、不落盘。
