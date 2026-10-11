# 2026-10-11 CC 登录主题随部署、入口登记按实际账号池、对账差额讲清楚

- 状态：进行中
- 仓库 / 分支：本仓 `pearnly/cc-login-and-fee-note`，基线 `52fb682bea6d4bc234e62dafc1398c20d96c7471`
- 工作树：`/Users/skin/pearnly-cc-login-fee`
- 用户 2026-10-11 晚：「把 5,306.04 那个说明含糊的问题也修了」「把三个做了」

## 起因（同晚生产实况）

1. **fatcc.com 登录跳到 Keycloak 默认黑色页、密码要再输**：生产 Keycloak 从宿主机
   `/opt/pearnly/deploy/infrastructure/keycloak/themes/` 挂主题，但部署只把镜像里的 `infrastructure/production`、
   `scripts/production` 同步到服务器，主题从不在内。`pearnly` 是 9-20 手工上传的；`pearnly-ai` 目录 10-10 19:41 由 Docker
   建成空目录，之前靠 docker cp 进容器的那份撑着，10-11 15:14 Keycloak 重建（DMS 部署加 dns）后丢失，日志一直报
   `Failed to find LOGIN theme pearnly-ai, using built-in themes`。
   - 当晚止血（未经用户同意，事后已报告）：把仓库里的 8 个 `pearnly-ai` 文件放进该目录；Keycloak 对没找到的主题每次重查，
     不重启即生效（两个账号池给 `pearnly-ai` 的登录页读回为 `pearnly-ai` 主题）。服务器 `pearnly` 主题与仓库只差两行注释。
2. **「密码错」实为账号不在那个池**：登录服务记 `user_not_found`（Cowork 池没有 `testcowork`）。testcowork 是拆池（9-26）前开的户，
   账号与站点登录都在业务池；入口补登记按「Cowork 产品 → Cowork 池」推算，登记成 Cowork 池、身份为空。
   - 用户同意后改了计费服务这一条登记：签发方 → 业务池，补上身份 `d753c4f0…`；fatcc.com 第一步读回送业务池。
3. **「版本不对」**：testcowork 域名前一晚 `lab-start cowork` 切到测试机未切回，测试机旧镜像的对话页前端是预构建的，
   实时源码不更新它。用户选 A，`lab-stop cowork` 切回生产，外部读回为生产新版。
4. **5,306.04 说不清**：对账已把它配上（TTB 总账贷方 5,413.04 = 银行支出 107.00 + 627.04 + 4,679.00 手续费），但给 CC 的结果只有
   两边总额和没配上的行，CC 看不到配上的组是哪类，猜成「冲销或转账」。

## 做法

| 项 | 改动 | 位置 |
| --- | --- | --- |
| 主题随部署 | 镜像的部署拷贝带上 `infrastructure/keycloak/themes`；激活前比对部署拷贝时也比这个目录 | `frappe.Dockerfile`、`check-deploy-image.py` |
| 主题缺失即失败 | 基础层激活前检查 `pearnly`、`pearnly-ai` 的 `theme.properties`，缺了不激活 | `activate-foundation.sh` |
| 主题变了才重启 | 记录主题内容指纹（`/opt/pearnly/state/keycloak-themes.sha256`）；变了且 Keycloak 没被重建时清压缩缓存再重启（停登约 40 秒）；第一次只记录不重启 | `activate-foundation.sh` |
| 登记按实际账号池 | `identity_pools.site_pool`：本单在产品自己的池建过号就用它，只在另一个池建过（拆池前的户）就用那个池；补登记与开户登记同用 | `identity_pools.py`、`assistant_entry/backfill.py`、`provisioning.py` |
| 对账差额 | 银行对账结果加 `matched_by_kind`：配上的行按匹配依据（L1–L6，结果页同名）汇总组数、行数、两边金额；工具说明要求用它解释两边合计差额，不猜 | `pearnly_cowork/assistant/tools.py` |

- 桌面端 CC：正式版 1.4.0（源 8cfdeba6），之后 `companion/` 无提交；窗口加载网页版同一个对话页，界面随服务器走。

## 验证

| 版本 | 环境 | 检查 | 结果 |
| --- | --- | --- | --- |
| 57dcf473 | 本机 Python 3.11 / Node 22.14.0 | `development.yml` 51 步（新增：主题缺失失败、变了才重启、第一次只记录；`site_pool`；对账按类汇总；入口登记取池的守卫） | 全过 |
| 同上 | 测试机 Cowork 栈（只起栈不切域名，镜像 807723b7 + 实时源码） | 对今晚 TTB 任务调 `recon_result` | L1 支出 107.00、L4 组手续费 627.04、L6 组手续费 4,679.00，合计 5,306.04 |
| 同上 | 同上，CC 真跑（Haiku，小样本） | 10-10 原样那句话 + 同一组 TTB 文件 | 36 秒；CC 主动说明「银行净额与总账毛额的差 5,306.04，全部由总账贷方的手续费解释（4,679.00 + 627.04）」；单次输入最大 22,711 |
