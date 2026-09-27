# 交接：Express 存货科目组「每个账套选一次」（2026-09-28）

上一窗口（ERP卡片标题和日期问题 / 负责 ERP + Cowork）收工交接。用户 2026-09-28 同意：**存货科目组做成每个账套选一次**，
新窗口开工。本窗口负责 ERP 与 Cowork；DMS 由「DMS 当前情况」窗口负责，**DMS 部署等用户通知**。

## 现在线上（2026-09-28）

- main `8b34f80c`；Cowork `b273e386`（代码 c610856a）；ERP `145269b5`；小助手正式通道 **1.2.3**；DMS 未动。
- 办公室测试机 `ssh lenovo-office-lan`（DESKTOP-MN2BRKR，SSH 用户 macadmin）：小助手 1.2.3 以桌面用户 skin3 运行，
  连 testcowork.pearnly.com（pearnlyai@gmail.com），默认账套 `S:\2569\EXP69\6902ASC`，授权 16 个年度文件夹，可写 574。
- 已核对的背景记录：[年度→账套 + 缓存](2026-09-27-express-catalog-select.md)、[LINE 打字找账套](2026-09-27-line-account-search.md)、
  [分店](2026-09-27-express-branch.md)、[按账套做库存检查](2026-09-27-express-account-prep.md)。

## 要做的事

用户口径：科目规则由会计定一次、日常自动套用、复核时看；存货科目组不是每张票选，而是**每个账套选一次**，以后新建库存品都挂这个组。

现状（先核实再动手）：
- `push/express/stock_acc_group.py`：候选 = 小助手上报的 `reported_stock_acc_groups`（ISACC 合格组）；`resolve_stock_acc_group`：
  配过 `stock_acccod` 就用它；合格组只有 1 个自动用；0 个 → `stock_acc_group_missing`；多个 → `stock_acc_group_required`。
- `push/express/mapper.py:229-240`（销项 `sales_mapper.py` 同理）：**只在账套零存货主档时**才解析 / 拦；有存货主档时不带组，
  小助手按已有库存品克隆模板（`companion/src/companion/stock_template.py`）——测试账套 2 个合格组时就是这样挑到 ST10 的。
- `stock_acccod` 现在是**连接级**单值（`connections/model.py` 配置键；`targets/choice.py` 换账套时清掉），不是按账套存；
  新平台**没有选组的界面**（旧版有「弹卡选一次」）。
- 非默认账套的候选组已按账套缓存：`report_json._account_caches[账套]`（`connections/collector.py`），推送准备
  `connections/preparation.py::_account_cache` 取用；选账套时（网页「继续」/ LINE）会申请小助手读该账套（1.2.3 起带指纹与合格组）。

建议方向（新窗口按实际代码定）：
1. 按账套存所选组（例如连接配置里 `{账套键: 组码}`，或按账套的独立记录），不再是连接级单值；换账套不再需要清。
2. 选择入口：网页新建录入选了 Express + 存货、且该账套合格组 ≥2 又没选过时，用原生下拉让用户选一次并保存；
   LINE 走同一规则（组少，可用快捷回复），或提示先在网页选。复核 / 设置处能改。**不加多余文案，原生组件。**
3. 选过的组在有存货主档的账套里也要带给小助手（新建库存品挂所选组，而不是随便克隆模板）；先读 `stock_template.py`
   与 `purchase_lines.py` / `dbf_sales.py` 确认小助手收到 `stock_acccod` 时建档的行为，必要时改小助手（再发版）。
4. 0 个合格组仍拦（要会计先在 Express 建组）。

## 验证方法（已授权）

- 用户授权：可往测试账套 `\\Accserver\d$\ACCOUNT\70EXP\TEST` 写入（合成测试票，明确标注）；测试账套税号尾号 0289，合格存货组 2 个，默认账套 6902ASC 合格组 13 个。
- 推送走 testcowork 网页编辑页「提交」；测试票可用 `pearnly_cowork.history.api.create` 手工建（不走付费识别），
  再按识别落库的做法写 `push_connection / push_account_set / intake_target`。
- 读回测试账套：SSH 用户碰不到共享盘。用以 `DESKTOP-MN2BRKR\skin3`、LogonType Interactive 的**一次性**计划任务
  只读复制所需表（APMAS / APTRN / ISVAT / STMAS / STCRD / ISACC…）到 `C:\ProgramData\…`，scp 回本机用 `dbf` 读；核完删任务和副本。
- 给测试机装小助手新版：核对 latest.json 的 sha256 后静默安装；**装完必须以普通权限（RunLevel Limited）重启小助手**，
  否则以管理员权限运行看不到 S: 盘（可写账套会从 574 掉到 51）。
- 共用层改动合入前在生产镜像跑完整 `ci-candidate-checks.sh offline`；Mac 打包用 `COPYFILE_DISABLE=1 tar --no-xattrs`。
- 部署：build-production → stage-production -f digest= → 服务器 `check-deploy-image.py` → `activate-product.sh <镜像> cowork`（ERP 有改动才激活 erp）。每次部署需用户同意。

## 保留项

- 测试账套里的合成测试数据：凭证 RR690927-001 / -002、供应商 ท031（分店 54）、库存品 PN00062；testcowork 里对应两张票 BR54-VERIFY-01 / STK-VERIFY-01。留作证据，用户要求再清。
- 测试机计划任务 `PearnlyRestoreCompanionConfig`：不是上一窗口建的，归属不明，未动。
- `.codex/worktrees/dms-fast-sync`、`dms-line-language` 与 `.claude/worktrees/*`：其他窗口的，未动。
