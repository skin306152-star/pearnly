# 上线车 11-14（集成分支 pearnly/integration-1114）

拼车窗口维护。部署不由本窗口发起，等主会话通知。线上现为 `6f58f0b48`（镜像 `sha256:054aa603…`，记录 [2026-11-13-release-6f58f0b4.md](2026-11-13-release-6f58f0b4.md)）。

## 这一车装什么（按进车顺序；「待进」= 还没推或 CI 没绿）

| # | 线 | 来源 | 状态 | 闸门 | 候选离线 | 流程 | 真机 |
|---|---|---|---|---|---|---|---|
| 0 | main 已合未上线：公司接入第 1 批（主档整读、erp-masters-worker）、MR.ERP 金额修复、收款闭环（单页收款、一个权限、拒收退回、后台回复语言）、性能体检（11-14 预批合入） | origin/main 12e71d3a9 | 已在 main | 见下 | 见下 | 见下 | 见「真机」节 |
| 1 | 公司接入第 2 批 | origin/pearnly/company-onboarding-b2d996 43d0ceda5 | 已进（CI 缺真机记录会红，主会话定进集成分支） | 逐行查询闸门：cc_service.create_many 待标有界 | | | 见「真机」节 |
| 2 | 引导式第 1 期 | origin/pearnly/cc-guided-phase1 97cc633bf | 已进 | | | 待跑 | 真模型三格窗口自测 1.43 美元 |
| 3 | 上传后才识别 | origin/pearnly/cc-upload-read-on-send-c21d68 88562fb05 | 已进 | 逐行查询闸门：files.start_sent 待标有界 | | | |
| 4 | 性能体检 | 已在 main 12e71d3a9 | 已在 main | 带来逐行查询闸门 | | | 上线后 testerp 复量 |
| 5 | MR.ERP 写账弹窗 + 现金采购 | pearnly/mrerp-ledger-dialog | 待进（未推）；mrerp-cash-purchase 不单独进 | | | | |
| 5' | ERP 作废缺口 | origin/pearnly/erp-void-gaps-1114 dde614495 | 已进 | | | | |
| 6 | 访问速度 C 启动词条 | origin/pearnly/boot-messages-cache 5c2fbb833 | 已进 | | | | |
| 7 | CC 半截单（服务端部分；CC 后发） | origin/pearnly/cc-desktop-half-doc-1ee3e3 18a14ddf4 | 已进 | 逐行查询闸门：两产品 status 逐行查半截单号，待改一次取回 | | | 测试机真跑待做 |
| 8 | 收款菜单发布 | origin/pearnly/rich-menu-image-check c3a248f5d | 已进（CI 缺真机记录会红，属预期） | | | | 见「真机」节 |
| 9 | 公司接入第 3 批（含 MR.ERP 开关） | pearnly/company-onboarding-3-307a0a | 待进（wip） | | | | testerp 真写 TEST2019 |

集成分支上本窗口自己的提交：接缝 1 真跨边测试 `companion/tests/test_seam_half_doc_server.py`（交半截单窗口收进其分支）。

## 本车之后（不进这一车）

- 「Not permitted」统一：源码统一 `_("Not permitted")`，译文只在 pearnly_core 留一份；泰 / 日有可见变化，先给用户看前后清单。单独小分支、一个窗口做。
- 产品地图真机记录规则收紧（验收体系窗口在改）。

## 上线前置

- 真机 1：LIFF 相机 3 行（共用 link.js，公司接入第 2 批 + 收款菜单）。
- 真机 2：收款写进 Express（NLTTEST）。
- MR.ERP 开关打开前：testerp 真写 TEST2019（写账验收清单 B 段，主会话排 testenv）。
- 生产只读 1：小写原因码（already_collected / invoice_not_found）影响范围。
- 生产只读 2：「有销售岗位、没勾收款」的人数（收款改成一个权限后这些人看不到收款页）。
- 上线后：testerp 性能复量（盘点明细 6–7 秒那页）。

## 新增进程与配置

- `erp-masters-worker`（队列 `erp_masters`，512M，profile erp）：线上没有，本车首次上。compose / provision-agent / activate-product / 实验室 / testenv / 容量检查已在 main；`deploy.sh --dry-run` 时核它被建出来、只有 ERP 租户机起。
- R2 密钥：安装包走 R2（访问速度 B）尚未进车；进车时在此登记。
- 启动词条：启动数据只带语言与指纹，整表走新接口（游客可调，已登记原因与守卫）；部署后浏览器首次加载多一次请求，之后缓存。

## 迁移与补丁（相对线上）

| 补丁 | 来源 | 说明 |
|---|---|---|
| pearnly_erp_portal.patches.v1_28_admin_collection_menus | 收款闭环（main） | 已绑定 LINE 的人重挂富菜单 |
| pearnly_erp_portal.patches.v1_28_stock_count_indexes → v1_29 | 性能体检 | 结存 / 盘点索引；性能窗口本地已改名 v1_29，推后替换 |
| v1_29_master_ledger_source、v1_30_mrerp_company_identity | 公司接入第 3 批（待进） | 与性能 v1_29 撞号，需改号 |

DocType 改动：Pearnly Assistant Session（引导式 business / business_at）、ERP Collection（attempt）。

## 回滚

部署前给线上打回滚分支（同 11-13 做法 `pearnly/rollback-<日期>` 指 6f58f0b48），出事 `deploy.sh` 以 6f58f0b48 的发布标记重部署。
补丁只加索引与重挂菜单、DocType 只加字段，回旧镜像不需要反向迁移；erp-masters-worker 回旧版后由 compose 去掉。

## 上线后必验

- 六家 ERP 租户：collection 页面（单页）、权限、LINE 收款卡；`erp-masters-worker` 在跑、队列不积压。
- 启动词条：三产品首页、列表页中文 / 泰文显示正常（erp/cowork.desk.boot-messages）。
- 盘点明细页时间（testerp）。
- 登录后 e2e。

## 集成记录

见共享笔记 `拼车-进度-11-14.md`（盘点表、冲突解法、接缝核对、口径差异清单）。

## 真机

执行：收款窗口（三星 SM-A146P + 测试机）；清单见共享笔记「拼车-上线前真机清单-11-14.md」（拼车 9 项 + 收款 3 项，约 1 小时 45 分）。
被测集成头：开测前写死在此。每项一个条目，格式：

```
### <产品地图行 id 或项名>
- 日期：
- 被测提交 SHA：
- 设备：
- 结果：通过 / 不通过 / 没测到（原因）
- 步骤：
- 截图：
```
