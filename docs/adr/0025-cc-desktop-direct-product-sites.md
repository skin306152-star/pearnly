# ADR-025：CC 桌面版直连产品站点（共用机器通道 + 共用 Express 写账规则），ERP 前台不经 Cowork

- 状态：已采纳（2026-10-23）。体验口径是用户 10-23 定的；架构与取舍由开发窗口按「按目标自己定」决定，依据写在下面。
- 关联工作包：[ERP 直连 CC 施工记录](../work/2026-10-23-erp-cc-direct.md)；前一版设计与验证见
  [ERP 当前台、Express 当账本](../work/2026-10-22-erp-express-front-design.md)。
- 取代：上述设计记录第 4.1 节「Cowork 当账本网关」（Cowork 域 `erp_gateway` 与 ERP 的站点间调用）。
- 前置：ADR-009（产品站点只装自己的 App、产品自己声明）、ADR-019（`/assistant` 三产品共用）、ADR-021（多台电脑、谁的活谁的电脑做）。

## 问题与约束

用户 10-23 原话整理：「CC 在 Express 那台电脑登录 ERP 的账户，然后在外用 ERP 的账户登录做业务，数据写入 Express。用户实际只需要登录 CC 和 ERP。
CC 的账号就是 ERP 的账号，或者 ERP 添加的用户账号。」完全不出现 Cowork，事务所现有的 Cowork 用法不受影响。

已确认的事实：

- **登录早就通**：ERP 员工（业务账号池 `pearnly-business`，老板在 ERP `team` 里发号）可以经 fatcc.com 进 ERP 站点的 `/assistant`
  （共用层页面，三产品共用，ADR-019）。CC 窗口就是这条路。
- **缺的是机器通道**：配对、认电脑、心跳报账套、领单、回执、按需请求、写者锁全在 `pearnly_cowork.companion` / `connections` /
  `push.native`，而且 `credentials._site` 只认 `pearnly_product == "cowork"`；CC 桌面版把方法路径写死成 `pearnly_cowork.*`；
  一台电脑只连一个站点（地址只存一份全局设置）。
- **推送账本与识别记录绑死**：`Cowork Push Record.history` 必填，加锁、状态投影、成功回写、排头查询都穿过 `Cowork OCR History`。
- **Express 写账规则本身是纯函数**：`push.express` 里 `prepare_express` 的依赖闭包（连同 `contracts.push / history / pos / line_items`、
  `push.receipts`、`targets.identity / account_match / stock_groups`）约 40 个文件、5900 行，只依赖标准库和 `pearnly_core.tax_documents`。
- 约束：ERP 站点不能导入 Cowork（ADR-009）；Cowork 推送是生产在跑的写账路径，行为不能变；CC 桌面版要轻量（老办公机能用）；
  身份、授权、外部写者锁、幂等这些迁移保护不关。

## 选项

| 选项 | 做法 | 不选 / 选的原因 |
|---|---|---|
| A 整条链下沉 | companion + connections + push native + 写账规则（约 1.6 万行、15 张 DocType）搬进共用层，推送账本把「识别记录」换成抽象源单 | 每条推送路径都要改、DocType 跨 App 搬（表名、补丁、回滚），一批之内无法充分验证生产写账路径；**不选**（作为以后 Cowork 迁到共用通道的方向保留） |
| B ERP 自己复制一套 | 机器通道与写账规则都在 ERP 里另写 | Express 记账规则两份维护，必然走样；**不选** |
| C 按性质拆（**选**） | 已经是纯函数、与产品无关的代码原样搬进共用层；新建精简的共用机器通道给 ERP 用；Cowork 现有通道不动 | 写账规则只有一份；Cowork 线上行为不变（搬移只改导入路径）；ERP 直接收 CC |

## 决定

1. **纯函数原样搬进共用层**（机械搬移，单独提交，不改一行逻辑）：
   - Express 写账规则 → `pearnly_core.express_rules`（文件头写明多产品共用）：`prepare_express` 的依赖闭包，以及 `choices`
     （要人选的项）、`wire_location`、`referenced`、`written_check`。
   - CC 桌面版线上约定 → `pearnly_core.cc_desktop.wire`：`queue_contracts`、`request_contracts`、`device_contracts`、`route_contracts`
     与归属判据 `serves`（原 `connections.member` 的纯函数部分）。
   - Cowork 所有调用方直接改导入新位置，不留旧路径壳（这些都不是对外 RPC、排队任务或持久化的点路径）。Cowork 自己的规则
     （自动推送档位、费用科目建议、公司规则、商品入账方式等）留在 `pearnly_cowork.push.express`，从共用层导入要用的部分。
2. **共用机器通道** `pearnly_core.cc_desktop`（文件头写明多产品共用；本批只有 ERP 接）：
   - **登记电脑**：人在 CC 窗口里登录站点后，对话页带本机设备密钥调 `pearnly_core.cc_desktop.api.register`（Frappe 会话 + CSRF）。
     一台电脑在一个站点一行（`Pearnly CC Device`，按 sha256(站点, 设备密钥) 认），只存令牌摘要；同一台电脑再登记只记登录人
     （`signed_in_users`），不换密钥；`rekey` 才换。谁能登记由产品声明（ERP：在职员工且有开单权限）。
   - **机器认证**：只接受精确的几个 POST 方法、`Authorization: Bearer pcc_…`、不带会话；按令牌摘要找到电脑，复核电脑没移除、
     最近登记的人仍在职（失效即 401）。做法照 Cowork `http_auth`：在 Frappe 的 OAuth 之前收走令牌、切到登记人身份、清 Cookie、
     错误不带内部信息。前缀 `pcc_` 与 Cowork 的 `exp_` 互不相认，两套钩子在同一站点上互不干扰。
   - **心跳**：电脑报自己够得到的账套（路径、文件号、公司、税号、分店、期间、能不能写、6 个默认科目）与版本；存成「电脑 × 账套」
     的可达行（`Pearnly CC Book`）。账套键：报了文件号用文件号，没报用这台电脑的路径。
   - **写账队列**（`Pearnly CC Job`）：产品把冻结好的载荷交进来（来源单据、录入人、账套键、请求号、载荷版本）；电脑领单时按
     共用纯函数 `owns`（录入人在这台电脑登录过 CC；没有登录记录的电脑过渡期谁的都写）、够得到、可写、载荷版本支持来判；
     每台电脑同时一件，同一账套同时一件（站点内写者锁）；领取发围栏号与一次性尝试令牌，回执凭它认；成功记单号与回读核对，
     等账套不计次数，普通失败重试到 3 次转人工，租约过期结果不明转人工不自动重写——与 Cowork 账本同一口径。
     回执经产品钩子交回产品（ERP 把单号、状态写回单据）。
   - **按需请求**（`Pearnly CC Request`）：产品请电脑读一本账套（`express_masters`），结果分块交回、存站点私有目录、取走即删；
     账套的公共资料，够得到它的电脑谁先来谁读。
   - 产品经 hooks `pearnly_cc_desktop` 声明：谁能登记电脑、回执与请求结果交给谁。共用层不认识任何产品的表。
3. **ERP 直连**（`pearnly_erp_portal.express`）：单据提交 → 按公司税号 + 分店 + 单据日期在 CC 报来的账套里选（同期两份按公司按
   年度问一次、记住复用）→ 用共用写账规则和电脑读来的账套资料准备载荷 → 交进共用队列（录入人 = 提交单据的人）；要人拿主意的
   （同期两份账套、没有 Express 编码的对方 / 商品、服务类进项的费用科目、部门）停在 ERP 单据上，在 ERP 里选，选过记住；
   回执把 Express 单号与状态写回单据；主档与库存经按需请求回读（沿用 10-22 的 `masters` / `locks`）。
4. **跨站点写者互斥在电脑这一侧**：同一份 Express 账套可能同时被事务所的 Cowork 和客户的 ERP 派单，两个站点的写者锁互不相识。
   CC 桌面版每次写入前在账套目录里锁一个锁文件（网络盘上同样有效），锁不到就按「等账套」回报、稍后自动再写。同一台电脑本来就
   单线程串行写。站点内的写者锁照旧。
5. **CC 桌面版（1.11.0）**：每个成员自带站点地址与通道（`cowork` / `core`），方法路径按通道取；同一台电脑可以同时连一个 Cowork
   站点和一个 ERP 站点（同一产品换站点仍要确认「改连」）；文件夹同步只给 Cowork 成员。`express_masters` 一并读回科目表、存货
   科目组与默认科目（准备载荷要用）。不新增常驻进程、不加网页内核。
6. **删除中转**：Cowork 域 `erp_gateway`、识别记录来源 `erp_portal`、网关服务账号与令牌、ERP 的 `express.gateway` / `express.setup`、
   站点配置 `pearnly_erp_gateway` / `pearnly_express_gateway` 一并删除。生产上从没有租户接过（10-22 记录），没有数据要迁。
   10-22 留下来的按码直落（`choices` / `common`）、`express_masters` 请求种类、ERP 的主档同步与只读锁原样留用。

## 后果与验证

- **两套机器通道并存（登记的债）**：Cowork 仍用 `pearnly_cowork.companion`，ERP 用 `pearnly_core.cc_desktop`。Cowork 迁到共用通道
  的条件：推送账本先把「识别记录」换成来源单据（选项 A 的前半），并有一次完整的多电脑真机回归；在那之前不在共用通道上加
  Cowork 专属能力，也不往 Cowork 通道里加 ERP 能力。
- **Cowork 推送不变的证据**：搬移提交只改导入路径；Cowork 全部单元测试、契约测试、架构检查通过；搬移前后对同一批推送用例的
  载荷逐字节相同（测试锁住）。
- **迁移保护**：机器令牌只存摘要、只能调精确的方法；登记人失权即失效；写者锁（站点内 + 电脑侧锁文件）、请求号幂等、围栏号与
  一次性尝试令牌、结果不明不自动重写都保留。
- **验证**：本地全闸门、分支 CI、CC 测试包；测试机真实写入只落 C 盘 `70EXP` 的 T 账套；Cowork 推送回归在同一台测试机上再写一张。
- **回退**：服务端回退到上一版即恢复；CC 1.11.0 连旧服务端时 ERP 成员登记不上（没有该方法），Cowork 成员照旧。
