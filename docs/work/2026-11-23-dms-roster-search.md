# DMS 操作员名册加按名字搜索（2026-11-23）

> 分支 `pearnly/dms-roster-search`，基线 `7d935d206`（= 开工时 origin/main）。主会话 11-23 派单并给定做法，今晚单独部署。

## 需求原文
真实客户（Korn）：「เมนูเพิ่มพนักงานให้มี ช่องค้นหาตามรายชื่อ เพราะพนักงานเยอะ เกือบ 100 คน เลื่อนค้าหา ลำบากครับ」——员工近 100 人，名册页要能按名字搜。

## 影响评估（简）
- **同类复用**：页头搜索框照 ERP 物料页 / 团队页（`page.add_field` + `list.reload()`），列表仍是共用 `p.list`（core `list.js`），不自造控件。
- **Frappe 原生**：服务端 `frappe.get_all(or_filters=[[字段, "like", "%值%"]])`（参数化），分页仍是 `limit_start` / `limit_page_length`；总数用共用 `list_filters.count`（COUNT 聚合，同一份条件）。
- **范围**：搜显示名 `display_name` 与 DMS 用户名 `dms_username` 两列；空串 = 全部（行为同原来）。
- **权限 / 隔离**：仍先 `_require_admin()`；只在本站点的 `DMS Operator` 里搜。
- **入口**：只有 DMS「พนักงาน」名册页（电脑与手机同一页）。LINE、CC 不涉及。
- **文案**：用 Frappe 自带「Search」（官方四语译文），无新增 / 改动词条；`check-copy` 拦截 0。
- **成本 / 数据**：无新存储、无外部调用；作废删除不涉及。
- **共用件**：只新增对 `list_filters.count` 的调用（已登记的「共用列表」件）；`check-impact` 影响清单：`dms.page.roster`（自动）、`dms.roster.line-oa`、`dms.line.binding`（这两项本次没改代码）。

## 改动
- `apps/pearnly_dms/pearnly_dms/operators/roster.py`：`list_operators(start, page_length, search=None)`、`api_list_operators(..., search=None)`。
- `apps/pearnly_dms/pearnly_dms/public/desk/roster.js`：页头加搜索框，值随分页参数交给接口；改值即从第一页重载。
- 测试：`tests/test_dms_roster_search.py`（只回匹配行、按用户名也能搜、分页仍生效且总数同条件、空搜索=全部、坏分页仍拒绝），进 `check-dms-offline.py`；`tests/test-roster-selection.cjs` 补搜索框与参数断言。

## 验证
（见下方，随提交更新）
