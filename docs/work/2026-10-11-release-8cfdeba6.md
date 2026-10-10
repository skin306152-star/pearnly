# 上线 8cfdeba6：CC（智能体）、fatcc.com 入口、CC 桌面正式版 1.4.0（2026-10-11 凌晨，曼谷 02:31–03:00）

main 8cfdeba6，镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:6507c9b948ff76e416b9e9ac29c2bb01ddb46722cc1ea4e51179b5cda0f7413d`
（构建 run 38079266181，含候选并行检查；stage run 38079680633）。用户 2026-10-11：「做完验证没问题再上线」，并同意
桌面端正式版、登录页主题重装（停登约 40 秒）、生产回调地址三项。日志 `/opt/pearnly/state/deploy-8cfdeba6.log`。

## 内容（c3a7622c..8cfdeba6，124 个提交，分支 `pearnly/agent-foundation`）

- CC 对话页（ADR-019）与 Cowork 工具：录入工作台全部、银行 / 收入对账、销项税核查、库存盘点、任务看板、看原件；
  改共用设置的一步先点「允许」（第 22 项）；引擎回调叫醒服务端执行工具（第 16 项）；工具执行按对话语言。
- 入口 fatcc.com（计费服务那套程序上的入口站点）、Keycloak `pearnly-ai` 主题改为宿主机挂载、租户站点登记。
- CC 桌面端 1.4.0（原小助手）：账号连接、设置页、文件夹选择与 Express 数据查找、PDF 预览。
- 详见 [工作记录](2026-10-10-assistant-foundation.md) 逐项清单。

## 关

- 分支 Development checks 每个提交都过；共用层改动合入前在测试机（线上同一份镜像 20087d9b）跑 `ci-candidate-checks.sh offline`：
  第一次拦下开户单校验的测试假单缺智能体字段（`684538f2` 补测试假单，生产代码不变），复跑 61 项全过；main 构建候选并行检查全过。
- 测试站（实时源码）+ 测试机：测试窗口逐项验收第 8–22 项；本窗口第 12 项真推（读回 Express ARTRN / ISVAT 一致）、
  第 16 项回调端到端（不开页面）。
- 没做：压测、故障 / 回退演练（不改识别读法与并发参数）。

## 部署与回读（UTC）

- 部署前：内存可用 21G；无非生产容器；镜像 revision 8cfdeba6；部署工具与镜像一致。
- 激活：ERP 19:31–19:35、Cowork 19:35–19:38、DMS 19:38–19:41，都 "activated with verified image"；
  基础层 19:41–19:42（计费备份 + 迁移；Keycloak 19:41:33 重建，主题 `pearnly`、`pearnly-ai` 都是挂载）；
  入口站点 fatcc.com 19:42–19:44 新建、装 pearnly_control、写入口配置（令牌不打印）；nginx `pearnly-fatcc.conf` 重装、`nginx -t` 通过。
  （第一次上传的部署脚本是空文件、什么都没执行，重传核对大小与哈希后才启动。）
- 回读：24 个产品 / 计费容器全为新镜像，4 个公共镜像（数据库、两个 Redis、Keycloak）；三个识别进程 NOFORK=1；
  六站 maintenance=0、部署后 Error Log 0；billing.internal、fatcc.com 部署后 Error Log 0；mariadb 3.44 / 8 GiB；
  mrerp / erpcowork / ris ping 200；fatcc.com 首页 200、其余接口 403（设计如此）。
- 租户登记：本机管理后台 `bench --site control.localhost execute pearnly_control.assistant_entry.backfill.run` 补登 6 个站点，
  计费服务读回 6 条（站点、账号池、管理员账号名与邮箱；testcowork 是拆池前的测试租户，无身份标识，按账号名找）。
- 入口实测：fatcc.com 用测试账号查到 Cowork 账号池登录页（客户端 pearnly-ai、回调 fatcc.com），登录页是 pearnly-ai 主题。
- 回退目标：三个产品与计费回 `…@sha256:20087d9b…`。

## CC 桌面正式版 1.4.0

- 服务端部署后发布（run 38081348157，源 8cfdeba6）；`https://pearnly.com/downloads/companion/latest.json` 回读 1.4.0
  （sha256 `4027668e…`），生产 Cowork `release.latest()` 读到 1.4.0：1.3.22 起的客户端下一次心跳就收到更新。

## 生产回调地址（第 16 项）

- 生产各站点都还没有智能体密钥（没有租户开通 CC），回调地址此时无处可接，未登记。哪家租户在管理后台开通 CC 后：
  在引擎控制台为那个站点登记回调地址 `https://<站点>/api/method/pearnly_core.assistant.webhook.receive`（订阅
  `session.status_idled`），把签名密钥写进那个站点配置 `pearnly_assistant_webhook_secret`。没登记时照旧由推送线程与页面兜底执行。

## 遗留

- 第 17a 项（对账引擎刷卡结算与手续费科目）：生产原有问题，另做，要先跑压测语料对照。
- testcowork 域名仍指测试机实验栈（实时源码），生产 testcowork 站点照常在；要在生产验收时 `testenv.sh lab-stop cowork`。
