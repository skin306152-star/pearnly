# apps/

Frappe Apps，按计划分四类，当前为空。

- `pearnly_core` — Site 运行身份、授权上下文、Book/WorkArea、OIDC 适配、任务与审计合同。不做装全部业务的巨石。
- `pearnly_cowork` — Cowork 产品：录入、识别记录、推送日志、对账、盘点、主数据与 Express 小助手。
- `pearnly_erp_portal` — ERP 门户产品。不因入口名字默认安装 ERPNext 全套业务。
- `pearnly_dms` — DMS 产品。
- `pearnly_control` — 新管理后台（Frappe 实现），仅装控制 Site，不装进租户 Site。现有 `/admin` 只作功能参考与迁移来源。

等 P1 的框架版本矩阵与代码归属 ADR 定稿后再创建，避免先按错误基线铺开。
