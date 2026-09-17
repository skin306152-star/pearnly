# P3.01 产品 App 与产品清单落位

日期：2026-09-17。

## 仓库里的五个 App

全部在本仓 `apps/`，经 `infrastructure/images/frappe.Dockerfile` 打进镜像（ADR-003 的落地）：

| App | 角色 |
|---|---|
| `pearnly_core` | 共用层：统一身份接入、会话与撤权、自助改密、产品清单加载 |
| `pearnly_cowork` | Cowork 产品 |
| `pearnly_erp_portal` | ERP 门户产品 |
| `pearnly_dms` | DMS 产品 |
| `pearnly_control` | 控制面（只在开发机，ADR-004） |

## 产品清单（P1 合同的代码化）

每个产品 App 里有 `product_manifest.py`，给出 `MANIFEST`：产品 id、入口前缀、依赖的
App、功能、模块开关、以及**明确不允许做的事**。

`pearnly_core/manifest.py` 负责加载与自检：

- `load_all()`：扫已安装 App，收集清单。
- `current()`：本站点的产品清单；**一个站点只能有一个产品清单**，多装直接报错
  （宁可启动即失败，也不要跑到一半才发现装错了产品）。
- `check()`：清单与本站点安装状态是否自洽。

清单里写死的不迁移项（与用户决定一致）：会计、报税、工单制、POS 收银、AI 管家、
Daily、工资、邮件接入、Excel 对账内测。ERP 清单的 `must_not` 里专门写了两条：
**不默认安装 ERPNext 全套业务**、**不生成会计凭证**。

## 安装结果（实测）

| 站点 | 装的产品 |
|---|---|
| `cowork-a`、`cowork-b` | `pearnly_core` + `pearnly_cowork` |
| `erp-a`、`erp-b` | `pearnly_core` + `pearnly_erp_portal` |
| `dms-a`、`dms-b` | `pearnly_core` + `pearnly_dms` |
| `control.localhost` | `pearnly_core` + `pearnly_control` |

四个站点跑清单自检，结果全部为 `PROBLEMS=[]`，产品识别正确
（cowork / erp / dms / control）。

## 自检抓到了我自己代码的一个 bug

第一版 `check()` 用"字段为真"判断必填，导致 `modules: []` 被误报成"缺少字段 modules"
（DMS 与控制面本来就没有模块开关）。已改为**判断字段是否存在**，空列表是合法值。
这条留在这里是因为它说明自检本身要能被信任，否则以后每加一个产品都会误报。

## 顺带记录

安装脚本里变量一度命名成 `GROUPS`，与 zsh 的内置数组同名，脚本被 zsh 执行时取到了
系统组 ID，表现为"组 20 -> "这种莫名输出。改名后正常。脚本已固定用 `bash` 执行。

## 未做（P3 余下）

1. **纵向切片**：还没从 176 表归属清单里挑每产品一条完整链路映射成 DocType。
2. **数据访问迁移**：业务代码还没从 psycopg2 / 全局连接池迁到 Site 内数据访问。
3. **兼容适配层**：旧前端的请求/响应合同还没做适配。
4. **unknown Host 拒绝**、Site 内部 ID 固定、域名更换不改身份——都还没做。
5. **角色与账套范围**：还没用 Frappe 原生 Role / DocType Permission / User Permission 表达。
6. **管理员交接**：依赖成员与角色模型（哪个角色能交接、交给谁），属 P4 权限管理，
   本轮不硬做半成品。
