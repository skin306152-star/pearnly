# apps/

Frappe Apps，按计划分四类，当前为空。

- `pearnly_core` — Site 运行身份、授权上下文、Book/WorkArea、OIDC 适配、任务与审计合同。不做装全部业务的巨石。
- `pearnly_cowork` — Cowork 产品，含 WeKan 集成。
- `pearnly_erp_portal` — ERP 门户产品。不因入口名字默认安装 ERPNext 全套业务。
- `pearnly_dms` — DMS 产品。
- `pearnly_control` — 统一后台，仅装控制 Site。

等 P1 的框架版本矩阵与代码归属 ADR 定稿后再创建，避免先按错误基线铺开。
