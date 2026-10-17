# 统一原件层（ADR-024）施工记录

用户 2026-10-18：「做统一原件层：所有产品所有入口都走共用层，按租户路线存谷歌或甲骨文，先给我施工顺序再开工。」
分支 `pearnly/originals-layer`（工作树 `.claude/worktrees/originals-layer`），基线 main ec90fdfc。

## 入口与落点

| 入口 | 存放 | 识别 / 预览 | 提交 |
| --- | --- | --- | --- |
| Cowork 录入台（PDF / 图片 / 表格直传） | `originals.store`：Claude → 甲骨文，谷歌 → 谷歌存储 | 甲骨文按引用读；谷歌由 Cloud Run 按版本号读 | 62780d19 |
| Cowork LINE、CC 工具建原件、POS 导入 | 同上（`store.put`，生产手上的字节交原件层） | 同上 | 62780d19 |
| Cowork 对账中心 | 登记文件（`originals.registered`），本机只留不到 1KB 的登记 | Claude 扫描件由甲骨文逐页读（`pages.frame`），谷歌由 Cloud Run 按引用读；表格、电子 PDF 仍在生产解析 | 15953187、c138c9f9 |
| ERP 采购 / 销售网页附件 | 官方上传框换成直传原件层（`recognition.originals`）；iPhone 照片经服务器转 JPEG | Claude 租户的附件都在甲骨文时只交引用（`read_originals`）；PDF 预览由甲骨文 / Cloud Run 画 | c138c9f9 |
| ERP LINE 收单 | `registered.put`；连不上抛 Deferred，按队列重试 | 同 ERP 网页 | c138c9f9 |
| 下载（所有登记文件） | `before_request` 先按 Frappe 私有文件规则核对权限，再转签名地址 | — | c138c9f9 |
| 删除 | 最后一条登记删掉时，File 扩展排任务删原件 | — | c138c9f9 |
| DMS 身份证 | 例外：不留存 | — | — |

生产与闸门一致：
- 甲骨文逐页出的图与 `PageSource.frame` 逐字节相同。
- 多附件「先转正、再出页」与 ERP 在生产上的做法逐字节相同。

见 `tests/test_recon_originals.py`、`tests/test_originals_registered.py`。

## 证据（2026-10-18，eb7ac4b2）

- **实现与单元测试**：新加 `test_recon_originals`（9 项）、`test_originals_registered`（15 项），已登记进 `ci-candidate-checks.sh`。相关老测试全过。
- **Development checks（本地全跑）**：通过。
- **候选离线检查**：在生产镜像 ebba5a40 上挂工作树源码跑，结果见交付说明。
- **甲骨文服务**：已部署新读法（按页读 / 多附件读，向下兼容），在容器内做了端到端实测：
  - 对账 PDF 第 2 页：6.9 秒，读出 2 行。
  - ERP 两份附件：14.5 秒，Haiku 升级到 Opus。
  - 页号、sha 不对和原件缺失都按预期拒绝；测试原件已删。
  - 目前生产 7 个租户都走谷歌，没有 Claude 租户受影响。
- **测试机实测（2026-10-18，lab 挂 dab9c244 源码、线上底座 ebba5a40、测试租户切 Claude）**：
  - Cowork 录入台：浏览器直传 originals.pearnly.com，识别正确；原件引用 `oracle://…`，本机没有 File。
    下载跳转到签名地址后取回 88,877 字节，与原件一致；删单后甲骨文原件跟着删了。
  - 对账中心：扫描版账单 + 总账。原件层存两份，识别按引用读（/v1/read），4/4 匹配、差异 0；对账结束后两份原件都删了。
  - ERP 采购（PDF）、销售（照片）：直传甲骨文，识别按引用读，全程没有从甲骨文取回原件；预览来自甲骨文。
    附件链接 `/private/files/ERO-…bin` 跳转后取回原文件；登记文件约 345 字节；删登记后原件也删了。
  - 测试数据都已删，两个测试租户已恢复原配置，lab 已停（交接给开发线）。
  - 甲骨文 8443 已对办公室 IP 放行（安全列表 + iptables），保留给测试机测 Claude 路线。
- **没做**：
  - LINE 收单、CC 工具的实机测试：要真手机，只有单元测试覆盖。
  - Cloud Run 新版：要认 `ERO-`、对账文档只交引用。
  - ERP 站点配置 `pearnly_cowork_ocr_remote` 和 `pearnly_cowork_originals_bucket`：谷歌租户 ERP 没配之前照旧存本机。
  - 合入 main、生产部署。

## 下一步

1. 变基最新 main（文案闸门合入后）→ 全闸门 → 合入 → 攒批部署。
   - Cloud Run 按修订版名切流量。
   - ERP 站点补两项配置。
2. 部署后回读：新上传原件落在甲骨文 / 谷歌存储，生产本机只有登记。
