# 部署时同步站外表面（富菜单等）——盘点与设计（11-26，未完成、不可合）

分支 `pearnly/deploy-line-menu-sync`，基 origin/main 71c46b036。**本分支只有本记录，没有代码改动，不可合入。**

## 背景与用户原话

- 11-26 晚 DMS 库存停放报表上线后发现：部署不会重发富菜单。ris（正式客户，25 个绑定）的 DMS 六宫格从 9 月开户后**从没更新过**，
  10-02 加的「使用手册」格、11-08 缩短的泰文标签、11-26 的「报表查询」都没到客户手上；testdms 也停在 11-08 之前的泰文标签。
- 用户原话：「这真是一个傻逼的开发流程」；「三个产品以后不允许出现这样的情况」。
- 硬要求（主会话转达）：Cowork / ERP / DMS 三个产品、所有租户、所有「用户看得到但存在站外」的表面（LINE 富菜单、LIFF、回调、登录主题等），
  部署后都必须与代码一致——由部署自动同步并回读，有漂移检查，失败即部署失败。盘点表每项落到「已由部署覆盖 / 本分支补上 / 明确不适用（理由）」之一。
- 11-26 晚的手工补发（主会话执行，脚本由库存报表窗口写、审后跑）：
  - 只读计划 `MODE=plan`：`~/pearnly-devtools/prod-ro-1126/dms-menu-plan.out`——ris 8 套全要新建（现挂的是 09-20 / 09-27 第一版，
    基础版 4 格、查询版 5 格，图片散列与仓库第一版逐一对上），testdms 5 套（4 套 query + th:basic，后者是 11-08 d838ea3da 泰文第 4 格标签缩短）。
  - 发布 testdms：`dms-menu-publish-testdms.out`；发布 ris：`dms-menu-publish-ris.out`（BEFORE 行可回退），两站 readback_ok，再跑 plan 无变化。
  - ris 发布前核过三个教程视频键都配了、6 个地址 HTTP 200。
  - 脚本：`dms-menu-publish.sh/.py`（plan / publish / rollback）、`dms-menu-diff.sh/.py`（逐格比对）。

## 盘点表（只读勘察，origin/main 71c46b036）

结论：**平时部署（deploy.sh → deploy-run.sh → activate-product.sh → activate-foundation.sh → deploy-readback.sh）只重新生效 Keycloak 登录主题文件**；
富菜单、webhook、LIFF、Keycloak 身份提供方 / 客户端都只在开户或改渠道（`site-step.py` 的 `set_line` 等）时写一次。
其中 webhook、LIFF 落地地址、Keycloak 各项**部署前已有内容级漂移检查且会拦**；**富菜单没有任何内容级检查**（只看「有没有」）。

| # | 站外表面 | 写入点 | 谁触发 | 部署重跑 | 现有漂移检查 | 存在 / 内容 | 归属（三选一） |
|---|---|---|---|---|---|---|---|
| 1 | DMS 富菜单 8 套 + 官方号默认菜单 | `pearnly_dms/menu/rich_menu.py` `publish` | 开户 `set_line`；人手 | 否 | `verify_site`（只开户）；L4 只看有无默认菜单；`pearnly_dms_rich_menus` 不在产品地图 | 编号 / 存在 | **本分支补上** |
| 2 | ERP 富菜单 60 套 | `pearnly_erp_portal/integrations/line_menu.py` `publish` | 同上 | 否（v1_28 补丁只重挂） | `verify_site` 只看配置键齐不齐；check-tenant-config presence | 存在 | **本分支补上** |
| 3 | Cowork 富菜单 28 套 | `pearnly_cowork/line/menu.py` `publish` | 同上 | 否 | 同上 | 存在 | **本分支补上** |
| 4 | 个人菜单挂载（三产品） | 各 `sync` / `reconcile` | 绑定 / 权限变化；DMS 每 10 分钟对账 | 否 | DMS 对账读 LINE；ERP / Cowork 只比 Redis | 编号 | **本分支补上**（发布后 `reconcile()` 已排重挂；回读抽查绑定用户） |
| 5 | Messaging webhook 地址 | `site-step.py` `set_webhook` | `set_line` | 否 | 部署前 L1 内容相等 + active（拦）；L6 测试推送（报告） | 内容 | **已由部署覆盖**（检查）；地址由站点名算出、代码改不动它 → 不需重写 |
| 6 | Use webhook 开关 / chatMode | 无写接口（LINE 后台手动） | 人手 | — | L1 / L2（拦） | 内容 | **明确不适用**：LINE 无写接口，只能检查（已检查） |
| 7 | LIFF 落地地址 / 类型 / 作用域 | 控制面 `onboarding/line_liff.py` `ensure` | 开户、改渠道、拆租户 | 否 | `check-liff-endpoints.py` 部署前后各一次（拦）；尺寸 / 作用域读不到 | 落地地址内容 | 落地地址**已由部署覆盖**（检查）；尺寸 / 作用域要 LINE Login 密钥、只在控制面 → **待定**：要么部署时经控制面调 `ensure` 对齐，要么定为不适用（理由：代码里应有值 10 月后没变过） |
| 8 | Keycloak 账号池 LINE 设置 | `configure-line-login.py` `configure_realm` | `set_line`；人手 | 否 | K5 `verify_realm`（拦） | 内容 | **已由部署覆盖**（检查）；漂移时拦部署、人手重跑 |
| 9 | Keycloak LINE 身份提供方 | `configure-line-login.py` `configure_site` | `set_line` | 否 | K5 按 `idp_representation` 比（拦）；映射 `line-user-id` 内容没比 | 内容 | 已覆盖；**本分支补**映射内容回读 |
| 10 | 站点 OIDC 客户端 | `keycloak_admin.create_oidc_client` | 开户、repool | 否 | K2 / K3（拦） | 形状 + 密钥 | **已由部署覆盖**（检查） |
| 11 | 账号池设置（主题名、记住我等） | `bootstrap-auth.py` | 人手 | 否 | K1 逐键（拦；SMTP 不读） | 内容 | 已覆盖；SMTP **明确不适用**（含密码，读回不安全；发信失败另有告警链） |
| 12 | 登录主题文件 | `activate-foundation.sh` | **每次部署** | **是** | 只记摘要 | 摘要 | **已由部署覆盖** |
| 13 | `pearnly-ai` 客户端、租户退出跳转 | `configure-ai-entry.py` | 人手 | 否 | K4 / K2（拦） | 内容 | **已由部署覆盖**（检查） |
| 14 | 用户 LINE 关联（federated identity） | `pearnly_core/account/line_login.py` | 绑定钩子 + 每小时对账 | 否 | 只看站点 `login_link` | 状态 | **明确不适用**：按用户、随绑定变化，每小时对账已在跑，与代码版本无关 |
| 15 | LINE Login 频道回调地址 | 无写接口 | 人手 | — | L5（拦） | 登记与否 | **明确不适用**：无写接口，只能检查（已检查） |
| 16 | 测试租户 Cloudflare 解析 / 证书 | `testenv-cloudflare.sh` / certbot | 人手 / 开户 | 否 | C1、证书天数（拦） | 内容 | **明确不适用**：与代码版本无关 |
| 17 | fatcc.com 租户登记 | `assistant_entry/registry.py` | 开户 | 否 | F1（拦） | 内容 | **已由部署覆盖**（检查） |

另：`scripts/check-line-labels.py` 只管按钮 20 字，不管 `chatBarText` 14 字（代码里 `[:14]` 截断）；没有任何检查拦「图 / 格子 / 词条改了但部署不发」。

## 设计（第 1–4 项）

1. **各产品声明自己的菜单表面**（ADR-009）：在 `hooks.py` 加 `pearnly_line_surfaces = ["<模块>"]`；模块提供
   - `wanted() -> {键: 菜单名}`：与 `publish()` **同一个**函数算（把 publish 里「body + 图 → 散列名」抽成 `_release()`，publish 与 wanted 都用它，避免两份算法）；
   - `drift() -> {behind, missing, default_ok}`：只读，GET `richmenu/list`（DMS 加默认菜单）对照站点配置编号；
   - `publish()`：现有函数不变（幂等、不删旧）。
2. **共用跑法** `pearnly_core/line/surfaces.py`（多产品共用层，文件头写明）：`check(site)` / `sync(site)`，按 hooks 汇总，单站失败抛错带站点与键。
3. **部署接入**：`deploy-run.sh` 在三个产品激活之后、foundation 之前加一步 `surfaces`：每个产品网页容器里对每个站点
   `sync`（有漂移才发布）→ 再 `check` 回读；发布前把原配置打进部署日志（同 `dms-menu-publish` 的 BEFORE 行，可回退）；任何一站失败 → 部署失败并打印停在哪一站、回退命令。
   `deploy.sh --dry-run` 只跑 `check` 报差异。`deploy-readback.sh` 再跑一遍 `check`，有落后即 PROBLEM。没有渠道凭据 / DMS 没 LIFF 的站点跳过并列出（同 `set_line` 条件）。
   ERP / Cowork 的图在发布时用 Pillow 拼，散列只在同一镜像里稳定——所以检查与发布都在生产网页容器里跑，不在本机算。
4. **闸门**：静态检查 `scripts/check-line-menu-sync.py`——改了 `public/images/menu|line-menu/**`、菜单 payload 模块或 `TXT_MENU_*` / 菜单词条，而产品没在 `pearnly_line_surfaces` 声明 → 拦；
   同时把 `pearnly_dms` 加进 `scripts/impact/product_map.py` 的 `CONFIG_SCAN`，`pearnly_dms_rich_menus` 进配置对比。部署说明写进 `docs/development/`（部署流程一节）。
5. **测试**：离线（替身 LINE）——有差异 → 发布 → 回读一致；无差异 → 不发布；发布失败 → `sync` 抛错、deploy-run 失败；无凭据 → 跳过并报告。候选离线、code-review medium。

## 做到哪 / 下一步

- 已做：盘点（上表）、设计、线上只读现状脚本 `~/pearnly-devtools/prod-ro-1126/line-menus-status.sh`（三产品 × 全部站点，算应有菜单名对照 LINE；**未在线上跑**，要主会话审后跑）。
- 未做：设计 1–5 的全部代码；三产品 × 正式租户现状表（等上面脚本的线上输出）；第 7 项 LIFF 尺寸 / 作用域的归属待定；第 9 项映射回读。
- 下一步顺序：① 主会话跑 `line-menus-status.sh` 出现状表，落后的先手工补发（`dms-menu-publish.sh` 只管 DMS，ERP / Cowork 同法补一份）；
  ② 实现设计 1–2（先 DMS，再 ERP / Cowork）+ 离线测试；③ 接 deploy-run / readback / dry-run；④ 闸门与部署说明；⑤ 全闸门、候选离线、code-review、合入。
