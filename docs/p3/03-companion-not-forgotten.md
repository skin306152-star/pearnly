# P3.03 小助手（Companion）不许漏迁

日期：2026-09-17。来源：用户提醒「pearnlycowork 里还有一个小助手，别迁移漏了」。

## 它是什么（核对旧系统的结果）

小助手是**装在商家会计电脑上的 Windows 程序**，不是网页功能。旧系统里能确认的行为：

| 行为 | 旧系统出处 |
|---|---|
| 安装包下载（登录鉴权，未登录 401） | `routes/companion_installer_routes.py` |
| 配对：密钥落在本机 `%APPDATA%\Pearnly\n.json` | 手册章节 `setup-n-pair` |
| 心跳上报：套账（账套 PATH 为唯一键）与科目映射 | `src/home/erp-express-wizard.ts`、`erp-agent-liveness.ts` |
| 出站拉取任务：`agent-token` / `heartbeat` / `lease` / `ack` | `routes/erp_agent.py` |
| 在本机会计软件里落库 | `erp-exc-actions.ts` 注释、手册章节 |
| 使用手册：安装/配对/版本升级/卸载与残留清理 | `static/dist/guide-content/{setup,maintain}.json` |

它在 Cowork 侧边栏里对应的是「**Express 推送手册**」这条线。

## 已经做的防漏措施

1. **写进产品清单**：`pearnly_cowork/product_manifest.py` 的 `features` 里新增
   `companion`、`companion_pairing`、`companion_account_reporting`、
   `companion_outbound_pull`、`express_guide` 五项。清单是"这个产品有什么"的
   唯一来源，不进清单就可能被漏。
2. **写进禁止项**：`must_not` 里加了两条——
   「把配对密钥或本机会计数据明文存到云端」、
   「让云端主动连入小助手（只允许小助手出站拉取）」。后者是安全边界，
   迁移时不能为了省事改成云端主动连。
   （2026-10-13 用户定，L1 回读：前一条改为「不把配对密钥明文存到云端；本机会计数据只在用户请求时按期间读取并私有留存」。）

## 迁移时必须一起带过去的四件事

1. **配对密钥的存放与轮换**：密钥在本机，云端只应存其校验值或引用，不能明文落库。
2. **账套唯一键是 PATH**：显示用公司名，判断用路径。这是旧系统明确的规则
   （`account_set` = 小助手所选账套的 PATH），不能改成按公司名匹配。
3. **只允许出站**：小助手主动拉任务、主动上报；云端不主动连入。
4. **本机写角色校验**：谁能在本机落库由小助手侧校验，迁移后这条不能松。

## 与 ERP 产品的关系

小助手会把单据推进外部 ERP，看起来像 ERP 的事；但**界面与手册挂在 Cowork 侧**，
且它服务的是"录入 → 推送"这条 Cowork 主线。因此按用户定的归属放在 Cowork。
ERP 产品只负责 `erp_push_logs` 这一侧的推送状态，两者不重叠。

**已记入待办**：Cowork 的纵向切片必须包含小助手这条链路，不能只做录入界面。
