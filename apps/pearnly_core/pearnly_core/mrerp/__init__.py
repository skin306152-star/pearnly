"""MR.ERP（泰国网页版会计软件 mrerp4sme.com）规则：多产品共用（Cowork 推送、ERP 接 MR.ERP；ADR-009 / ADR-025 / 评估
docs/work/2026-11-02-mrerp-operator-and-erp-company-评估.md §14.6）。

由 Cowork `connections.mrerp_*`、`targets.mrerp_*`、`push.mrerp` 原样搬来（2026-11-02），逻辑一字未改：登录会话（`session`）、
地址校验（`urls`）、账套目录（`catalog`）、主档读取（`master_reader`）、导入菜单与主档默认值的规则（`menus` / `masters`）、
推之前要人选的项（`needs`），以及导入直写（`push`：方向判定、模块表、导入文件生成、预览 / 报告 / 回读解析、对方与商品自建、
导入 5 步）。不连库、不依赖 Frappe、不认识任何产品的表；连接、凭据、推送记录、排队与要人选的记忆留在各产品里，只经参数交进来。
"""
