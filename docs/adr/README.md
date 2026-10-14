# ADR

登记已选定的架构决策。

已定稿：

- [ADR-001 原生 Frappe Site 路线](0001-native-frappe-site.md)
- [ADR-002 目标机与外部依赖保留范围](0002-deployment-target.md)
- [ADR-005 前端架构：Frappe 原生后台打底 + 新建业务页面用 Vue 3](0005-frontend-architecture.md)
- [ADR-006 语言管理：跟着账号走，缺译错译要纠正](0006-language-management.md)
- [ADR-007 前端皮肤：色值整份沿用 pearnly-app 的设计系统](0007-frontend-skin.md)
- [ADR-008 云端计费、本地控制面](0008-production-billing.md)
- [ADR-009 各产品自己声明菜单、功能目录与术语表](0009-product-owned-declarations.md)
- [ADR-010 DMS 后台完整读快照](0010-dms-background-read-snapshots.md)
- [ADR-011 手动建缓存，交互读本地，提交查实时](0011-dms-manual-read-snapshots.md)
- [ADR-012 票种与业务处理：全平台一张决策表](0012-shared-tax-document-rules.md)
- [ADR-013 识别读法：便宜模型读、代码兜底、按需复核，默认不用 3.8](0013-ocr-read-strategy.md)
- [ADR-014 DMS 会话复用、白天增量同步与账号并发](0014-dms-session-reuse-live-sync.md)
- [ADR-015 Cowork 发票识别在 Cloud Run 上执行](0015-ocr-on-cloud-run.md)
- [ADR-016 Cowork 原件由浏览器直传谷歌存储](0016-originals-direct-to-cloud-storage.md)

待定稿：

- **ADR-003** 代码权威归属与复用方式：Cowork、ERP、DMS 与 Frappe 自有层全部落在本仓，本仓是唯一权威位置；`pearnly-app`、`pearnly-erp` 等来源仓库转只读来源，不是业务、不并行维护；需逐项写明每份业务逻辑的落点（P1 定稿）
- **ADR-004** 控制面只在开发机运行：`pearnly_control` 不部署到云服务器，只做开户与授权，仅本人使用；云上只跑三产品 Site 与 Keycloak（团队协作看板 2026-09-21 起不再迁移）。需写明跨网开通通道、凭据保管、控制面数据备份与权威归属

注：ADR-004 虽未写成独立文件，但**已经在执行中**——多处文档与实现按它约束
（控制面无公网域名、不进服务器资源预算）。缺的只是成文与"跨网开通通道、凭据保管、
备份与权威归属"这几项的正式记录。

2026-09-19 补充：ADR-008 经用户确认，允许云端独立计费 Site 复用 `pearnly_control` 的计费表与规则；它不提供 Desk、开户或用户管理。管理页面和人工审核仍在本机。此决定更新上文 ADR-004 对整个 App 不上云的旧描述，不将整套控制面搬上云。

已确认但不重复询问的事项见计划包 `10-IMPLEMENTATION-HANDOFF.md` 的「已确认，不要重问」。
- [ADR-017 识别引擎可切换：新增 Claude（Anthropic）供应商与「只读一遍」读法（已废止）](0017-ocr-engine-claude.md)
- [ADR-018 Claude 批处理识别（已废止）](0018-claude-batch-delivery.md)
- [ADR-019 Pearnly 智能体底座（Claude Managed Agents + assistant-ui，三产品共用）](0019-assistant-foundation.md)
- [ADR-020 CC 按公司主档组织对话（全所主档、知识库、按电脑授权文件夹、按月处理）](0020-cc-company-master.md)
