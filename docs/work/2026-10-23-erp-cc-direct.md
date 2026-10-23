# ERP 直连 CC 桌面版、写进 Express（不经 Cowork）施工记录（2026-10-23）

- 状态：进行中。
- 分支 `pearnly/erp-cc-direct-26da5a`，工作树 `.claude/worktrees/erp-cc-direct-26da5a`，基线 `2dca095a`（= origin/main；生产 `d92337b2`、
  镜像 `0ccdb2a0`、CC 1.10.0 正式版，见 [发布记录](2026-10-22-release-d92337b2.md)）。集成目标 `main`。
- 架构决定：[ADR-025](../adr/0025-cc-desktop-direct-product-sites.md)。

## 目标与完成条件

用户 10-23：ERP 员工用 ERP 账号登录 CC 和 ERP，ERP 开的采购 / 销售单写进 Express，单号回到 ERP；主档、库存回读进 ERP。完全不出现
Cowork；事务所现有 Cowork 用法不受影响。由哪台电脑写照 Cowork「谁的活谁的电脑做」（ADR-021 第 50–55 条）。

完成条件：上面这条在测试机真实写入跑通；Cowork 中转（`erp_gateway`）已删；Cowork 现有推送回归通过；本记录分层写清实现、本地测试、
部署、外部结果与没验证的部分。

本次不做：领料（OU）、ERP 作废同步到 Express、部门 / 2–5 档售价 / 付款条件 / 业务员映射、Cowork 迁到共用通道（ADR-025 登记的债）。

## 施工顺序（按依赖）

1. 机械搬移：Express 写账规则与 CC 线上约定的纯函数进共用层（`pearnly_core.express_rules`、`pearnly_core.cc_desktop.wire`），Cowork
   改导入。单独提交，Cowork 全量测试 + 载荷逐字节回归。
2. 共用机器通道 `pearnly_core.cc_desktop`：DocType、登记、机器认证、心跳、写账队列、按需请求、产品钩子。
3. ERP `express` 域改直连：选账套、准备载荷、交队列、回执写回、要人选的项在 ERP 里处理、主档经按需请求。
4. CC 桌面版 1.11.0：每成员站点与通道、同时连 Cowork + ERP、账套锁文件、`express_masters` 带科目表等；对话页 ERP 站点自动登记。
5. 删除中转（Cowork `erp_gateway`、`erp_portal` 来源、ERP `gateway` / `setup`、两个站点配置键），文档与架构策略同步。
6. 验证：本地全闸门、分支 CI、CC 测试包、测试机真实写入（只写 C 盘 `70EXP` 的 T 账套）、Cowork 推送回归。
7. 上线：用户同意后照生产部署步骤，先服务端再发 CC 正式版；不替用户决定接哪个租户。

## 实现（第 1–5 步）

| 位置 | 内容 |
|---|---|
| 机械搬移（两个 refactor 提交，逻辑一字未改） | Cowork 的 Express 写账规则（`push.express` 里 `prepare_express` 的依赖闭包 24 个文件、`contracts.history / push / pos / line_items`、`push.receipts`、`targets.identity / account_match / stock_groups`）→ `pearnly_core.express_rules`；CC 线上约定（`queue_contracts` / `request_contracts` / `route_contracts` + `serves`）→ `pearnly_core.cc_desktop.wire`；回执口径（`push.lifecycle` 里的 `acknowledgement` 与转人工码表）→ `wire.ack_rules`；上报净化（`connections.reports` 里的科目表 / 存货科目组 / 目录 / 指纹 / 账套行）→ `wire.report_rules`；`transaction.conflicts` → `pearnly_core.transaction`；`companion.release` → `pearnly_core.cc_desktop.release`。Cowork 调用方全部改导入新位置，不留旧路径壳；`architecture-policy` 去掉 Cowork 的 `transaction` 域。ERP 网页容器也只读挂 CC 桌面版发布目录（心跳回最新版本）。 |
| 共用机器通道 `pearnly_core.cc_desktop`（新） | 四张表 `Pearnly CC Device / Book / Job / Request`（只经本域写，`documents`）；`devices` 登记电脑（对话页 + 设备密钥，created / exists / rekey / 签发人失权自动换 / removed 与 rejoin）；`http_auth` 机器认证（`pcc_` 令牌、精确 6 个 POST 方法，做法同 Cowork）；`books` 心跳账套名单 → 可达行（按文件号认一份账套）；`jobs` 写账队列（归属 `route_contracts.owns`、站点内写者锁 `writing_key` 唯一约束、每台一件、回执 `ack_rules`、租约过期钉回原来那台，线上单据号就是这件的编号，CC 本机日志去重）；`requests` / `store` 按需请求（线上形状同 Cowork）；`machine` 六个机器方法；`api.register` 给对话页；`products` 读产品声明 `pearnly_cc_desktop`。 |
| ERP `express` | 去掉 `gateway` / `setup`（站点间调用与运维接线）；`target` 选账套、`facts` 存读回的整套主档并转成写账规则认的账套资料（共用净化）、`choices` 要人选的项按公司记（新表 `ERP Express Choice`）、`prepare` 用共用写账规则备载荷、`push` 交共用队列与记结果（`job_changed`，不再轮询结果）、`masters` 经按需请求读主档、`desktop` 接通道的声明（在职员工、有开采购或销售单的权限）、`api` 单据上的 Express 弹窗（状态、单号、原因、要选的项、再写一次）。单据加 3 个隐藏字段（`express_job` / `express_book` / `express_needed`）。接没接 Express 由「本公司的账套有 CC 报过」定。 |
| Cowork | 删 `erp_gateway` 域、识别记录来源 `erp_portal`（`SOURCES`、DocType 选项、`storage.create_gateway`、自动推扫描的排除）、两条网关词条与两个站点配置键；10-22 的按码直落（`choices` / `common`，已在共用层）与 `express_masters` 请求种类留用。新增词条 `intake.error.ACCOUNT_WRITER_BUSY`。 |
| CC 桌面版 1.11.0 | `channel.py`（按密钥前缀分 Cowork / 共用通道，只换路径）；成员记自己的站点与通道（旧文件读时取全局那一个）；同一种通道换站点才要「改连」；文件夹自动录入、公司文件夹只给 Cowork 成员；`account_write_lock.py` 写期间锁账套目录里的 `PEARNLY.LCK`，锁不到按「等账套」回报；`express_masters` 结果版本 2 另带 `facts`（科目表、默认科目、存货科目组）；桥的 `features` 加 `sites`。 |
| 对话页 | ERP 站点（`product = erp`）每次打开同样登记这台电脑（`pearnly_core.cc_desktop.api.register`，配对码 `pcc_…@站点`）；设置里只列连着本站的成员；CC 桌面版太旧（没有 `sites`）提示更新。 |

## 本地验证（代码 = 本分支，环境 = 本机 Mac）

| 检查 | 结果 |
|---|---|
| 机械搬移：搬走的 34 个文件与原文件比，除导入行与模块名外逐字相同 | 0 处不同 |
| 机械搬移：全部 `tests/test_*.py`（328 个）搬移前（`2dca095a`）后逐个比对，本机两种跑法（带 / 不带 PYTHONPATH）与生产镜像 `0ccdb2a0` 里各跑一遍 | 本机 179 / 179、121 / 121 前后一致；镜像里 279 / 279 一致（另有 6 个前端测试因本机装了 Mac 版 node_modules 在 x86 镜像里跑不了，与改动无关，挡掉 node_modules 后同样） |
| 新增 `tests/test_cc_desktop_channel.py`（16 项）：令牌 / 账套行、登记四种情形、写账队列（谁的活谁的电脑做、同事的电脑不写、同一账套同时一件、等账套不计次数、3 次转人工与再写、租约过期钉回原来那台、迟到回执、旧版不领新载荷、别的电脑的回执拒收）、按需请求分块交回、机器方法只认 `pcc_` 与精确路径且与 CC 那张表一致 | 通过 |
| 新增 `tests/test_erp_express_direct.py`（10 项）：ERP 单经共用写账规则出载荷（采购未付 RR、销售已收 HS、对方档 / 存货照码、02 仓、载荷 4、借贷相等）；没有账套、主档没读过（请电脑读一次）、同期两份（选一次后照选的写）、服务类进项要选费用科目（选后借方是选的科目）、坏单据 | 通过 |
| CC 桌面版全量（Mac，Windows 专属的跳过） | 1060 通过、42 跳过（新增 `test_sites_and_channels.py` 7 项） |
| 本地全闸门（`~/pearnly-devtools/gate.sh`，照 `development.yml` 全跑） | 114 步全过 |
| `check-copy` | 新增 48 条、拦截 0 |

## 分支 CI 与候选检查（代码 = `d9d7bb94`）

| 检查 | 结果 |
|---|---|
| Development checks（run 39917885527） | 通过 |
| Companion checks（run 39917885628） | 通过 |
| CC 测试包 Companion test build（run 39917899955，`workflow_dispatch`） | 通过（首跑冒烟「second launch did not exit」偶发，重跑通过），产物 `CC-Setup.exe` 1.11.0 |
| 共用层候选离线检查（生产镜像 `0ccdb2a0` 里跑完整 `ci-candidate-checks offline`） | 退出码 0 |

## 测试机真实写入（环境 = 办公室测试机，实时源码 = 本工作树，底座镜像 `0ccdb2a0`）

写入只落 C 盘 `C:\PearnlyExpress\70EXP` 的 T 账套；局域网 Accserver / S 盘未写。CC 桌面版 1.11.0 测试包以 skin3（普通权限）
运行，同时连两个站点：Cowork 成员 → `testcowork.pearnly.com`（设备 `CCD-6f142429ee0742838e88`），ERP 成员 → `testerp.pearnly.com`
（共用通道设备 `PCD-d087e8c8522ef343276a`，由 ERP 员工账号在对话页登记，登记结果 `created`）。两成员每分钟心跳各自上报账套 679 份。

| 场景 | 结果（ERP 回读 + Express DBF 读回） |
|---|---|
| ERP 采购（未付，库存 USD × 3，02 仓）`PUR-2026-00111` | 写入 `RR691023-001`（T9NIEX12，นิภา เอ็กเช้นจ์ 分店 00012）；ERP 单状态 written、单号回填；队列件 success、核对 ok、写的电脑 = PCD 那台；APTRN 108.00 + VAT 7.56 = 115.56，STCRD 02 仓 3 |
| ERP 销售（已收）`SD-00113` / `SD-00115` | `HS691023-001` / `HS691023-002`；ARTRN、STCRD 02 仓各 1 |
| ERP 销售（未收）`SD-00117` | `IV691023-001`；排队 → CC 领单 → written |
| 要人选的项在 ERP 里处理：服务类进项 `PUR-2026-00119` | 先停在「要选费用科目」，在 ERP 选 `53-05-18-00` 后写入 `RR691023-002`；GLJNLIT 借 53-05-18-00 200.00、借 VAT 14.00、贷 21-02-01-00 214.00 |
| 主档与库存回读 | 公司 `CO-00078` 绑 `T.9 นิภา เอ็กเช้นจ์ (สาขา 00012)`，经按需请求 `express_masters` 同步；USD 在 02 仓结存 ERP = 2，与 Express STLOC（01：0，02：2）一致 |
| Cowork 推送回归（搬移后 Cowork 自己的通道，`lab-start cowork`）：手工票 → `choices_view` → `submit` → `confirm` | `CWP-b6ed…` success，写入 `RR691023-003`（同一份 T9NIEX12），载荷 4、核对 ok、写的电脑 = CCD 那台；分录形状与 ERP 直写 `RR691023-001` 完全相同（库存科目借贷、VAT、应付） |
| 两站点写同一份账套的锁 | 写入后账套目录有 `PEARNLY.LCK`（空文件，Express 不读）；锁不到回「等账套」由离线测试覆盖，测试机未造两台电脑同时写 |

## 没验证的部分

- 真人用 ERP 员工账号在 CC 窗口里登录 ERP、在 ERP 页面点开单 → Express 弹窗的整段点击：交给验收窗口由用户试（我不在登录页代输密码）。
- 两台电脑同时写同一份账套（锁文件跨机互斥）只在离线测试里验证。
- 生产部署、CC 1.11.0 正式版发布：未做，等用户同意。

## 记录

- 10-23 测试机验证完成后，按用户要求 `testerp.pearnly.com` 与测试机 CC 保持切在测试机，另开窗口做测试验证与验收。
  测试机 CC 原配置备份 `C:\PearnlyStress\_备份\cc-config-2026-10-23-erp`；验收结束后 `lab-stop erp`、刷新 DNS、按需还原。
