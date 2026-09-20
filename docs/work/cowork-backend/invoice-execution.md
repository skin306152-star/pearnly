# 发票识别执行与前端接入合同

2026-09-21，第四批。固定旧来源 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；本批实现属于 Cowork，未接界面、未部署或调用真实供应商。纯识别规则见 [engine](invoice-engine.md)，输入与供应商协议见 [IO](invoice-runtime-io.md)，授权例库见 [feedback](invoice-feedback.md)。

## 入口与实际流程

入口前缀 `/api/method/pearnly_cowork.ocr.api.`，遵循原生 Frappe message/权限/CSRF。

| 方法 | 输入 | 返回及权限 |
| --- | --- | --- |
| `submit` POST | 已上传 `source_file`、1–128字符 `request_key`、可选 `direction/posting_kind` | intake角色+购买功能+当前公司/买方+整件原件权限；`accepted=true`和任务投影，或者`accepted=false,code`（余额不足/查询失败/排队不可用分别表达） |
| `detail` GET | `name` | 重新检查整件权限；仅返回状态、物理页数、历史记录ID、是否需要复核和计费状态，不返回提示、凭据引用或模型原文 |
| `retry_billing` POST | `name` | intake及整件权限；只补已经保存结果的计费回执，沿原reference，不识别、不建第二批草稿 |

客户不能指定actor、租户、模型、运行配置、AI原文或计费页数。服务端按原件固定工作区/买方，保存不可变 `Cowork OCR Batch`；一种原件只有一个Batch和一个Core `cowork.invoice` Work。不同参数复用同一原件拒绝，同请求重放原ID；失败/结果未知任务不能通过重复submit重置。

流程为：原件→冻结配置/日期/授权纠错样例→余额检查→Core原生队列→逐页识别/检查点→冻结完整结果→同事务保存全部草稿/原件关联清单/Batch回执→按物理页数及原A/B档记账。请求锚定日期按来源TZ（缺省UTC）冻结，最终历史保存沿该年份。history首存ai_raw不可改；用户保存后原生纠错事件只读取已保存版本。

`saved`只表示可复核草稿已保存，不表示人工确认、推送或入账。`billing_state=pending`时识别成果仍可回读，队列只重报账；即使Work超时失败，`retry_billing`仍可补回执。全非票为`not_invoice/not_billable`，保留原件不生成票据。无有效识别字段、缺页或失败页拒绝整件，不生成空票收费。

## 运行与保护

- Site必须显式配置`pearnly_cowork_ocr_environment`（白名单字符串）与`pearnly_cowork_ocr_policy`；当前接economy/enterprise + Vertex，自动按套餐选档、其它provider、double-read/image-first在受理前拒绝。凭据引用沿Core配置，秘密不写Batch。新平台没有自动复制生产Secret或配置。
- PDF/image原件沿100MiB、50物理页上限；文本快路先用固定pypdf，普通PDF保持200DPI，单页超过40M渲染像素显式拒绝；按需取页，不一次持有50张PNG。document/table的虚拟页映射未接，受理明确拒绝。
- 每次供应商调用前结束数据库读快照、重新设置actor以清除Frappe本进程角色缓存，并检查当前功能/角色/范围/原件和冻结连接。长任务撤角色、撤公司权限、停用用户均不能沿旧缓存继续收费识别。
- Core预算、队列、租约与检查点共用既有实现。同Work串行，页面/阶段/尝试/真实供应商目标参加检查点键；完成响应重放，确定性拒绝也保留终态，后页限流不能把前页失败转成再次付费。429/503及明确Vision配额错误延后，结果未知停为uncertain，不自动切模型重做。420秒主动让出以避开Core 600秒硬超时。
- result先冻结，历史+原件清单+Batch保存标记同事务；保存失败不得收费。记账使用Core唯一billing_client，稳定reference绑定Batch；账单响应丢失只重取回执。旧staged路径本来跳过跨上传历史缓存，本入口保持此边界；非staged全局缓存未冒充已实现。
- Batch/Correction通过原生控制器、对象能力及查询hook拦截通用CRUD/分享。Cowork CoreWork mixin和生命周期guard阻断原生重置检查点、改归属或删除（含Administrator文档操作）；不改变DMS Work。原生升级只撤销Cowork Work遗留DocShare四个权限位，保留行；受理拒绝同稳定Work名预置的有效分享。平台Administrator全库维护权不当作客户权限。
- Core `_notify`补空hook返回`[]`的处理，避免Cowork失败/未知任务在释放容量前异常；真实DMS回调参数和行为维持原样。其它Core默认档位、DMS代码未改。

## 验证与限制

专用lab：Frappe16.33.0、Python3.14.7、MariaDB11.8、Redis6.2、pypdf6.15.0；768MiB bench，内部网络、无端口、无worker/scheduler、无真实凭据。运行定义和数据卷归本任务。pypdf是锁定镜像中已存在的同版本，本批只在Dockerfile显式锁定，未升级它或重发镜像。

22个真实Site执行场景通过：原生提交/处理/账单、两进程并发受理、重复请求、整件原子保存中断与恢复、已完成页遇限流续跑、网络未知不重做、未配置/auto拒绝、角色及公司撤销、冻结纠错提示、空识别和全非票不收费、回执丢失及Work过期补记账、HTTP鉴权/方法/原生详情拒绝、Admin重置拒绝、遗留分享升级只影响Cowork、默认买方不越界，以及50页合成PDF完整清单/100次假供应商请求/一次50页计费。16个原生纠错场景另见feedback记录。

50页用小尺寸合成PDF验证完整性和流程，不代表100MiB、大幅扫描页或多租户峰值容量已验收。供应商、账单均假出口；没有真实模型质量、真实扣费或浏览器/用户验收。Core原始provider检查点按既有七天保留策略清理成功任务；Batch长期保留票据结果及原响应digest/token证据，不重复存整份提示/图像。`estimated_cost_thb`不是本批实际供应商成本，不能用于展示实付金额。

真实旧例库/历史数据回灌、正式队列worker接线运行验收、付费识别抽样、可搜索PDF、表格回导、非staged缓存、外推/对账/LINE等仍是后续项。没有新增菜单，不能把这批后端API称为整产品前端可交付。
