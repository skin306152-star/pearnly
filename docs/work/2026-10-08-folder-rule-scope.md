# 自动录入规则：文件夹核对与默认值范围（2026-10-08）

分支 `pearnly/folder-rule-scope`，基线 `caef26b9`（= origin/main）。用户 2026-10-08 问「小助手授权的文件夹，网页端这边选择是不是对的，
业务逻辑会不会出错」，复核后批准三项（「可以，按你的推荐做」）。

## 复核结论（不改）

- 网页选的是「小助手报上来的授权根 id + 根下相对路径」，路径只在小助手本机解析，服务端不存本机路径 —— 映射正确。
- 同一台电脑上嵌套 / 重复的授权根小助手已拒绝（`folder_store.set_all_roots`，跨该电脑所有成员）；同根内规则文件夹重叠服务端已拒绝。
- 推送走与网页同一入口（`application.choices_view` / `submit`），税号闸门挡放错文件夹的票，基线挡移动过的旧文件。

## 改动

| 项 | 做法 | 位置 |
|---|---|---|
| 跨电脑重叠（两台电脑授权同一网络共享） | 小助手报每个授权根的网络路径（UNC 原样；映射盘查 HKCU\Network\<盘符>\RemotePath；本地盘不报）；服务端保存规则时，同工作空间其他连接的启用规则按网络路径比较嵌套，重叠即拒绝并说出对方规则名 | `companion/folder_plan.share_path`、`folder_worker._report`；`folder_intake/contracts.share_path` / `path_nested`、`rules._no_overlap` |
| 文件夹不可用要看得见 | 小助手每次同步报各规则状态（授权根不在 / 规则文件夹不在 / 正常），列表状态显示「文件夹不可用」；跑一次时文件夹不在记 `folder_missing`，运行记录同样提示 | `folder_worker._folder_health`、`_run`；`contracts.health`、`agent.remember`、`machine._sync`、`rules.listing`；`intake-folders.js` |
| 规则默认值只管本规则的票 | 「这一类以后都这样处理」设的付款 / 税 / 科目 / 商品 / 部门作为这张票的固定选择传给推送（优先于账套记忆），不再写进账套记忆，网页手推不受影响 | `push/express/choices.resolve(fixed=)`；`application.choices_view` / `prepare` / `submit(fixed=)`；`folder_intake/push.push_one` |

文案（用户已批）：`cowork.folder.folderMissing` 文件夹不可用 / ไม่พบโฟลเดอร์；`cowork.folder.overlap_with` 与规则「{0}」的文件夹重叠 /
โฟลเดอร์ซ้อนกับกฎ «{0}»（替换原 `cowork.folder.overlap`）。

限制：一台用映射盘、另一台用 UNC 指向同一共享可比较（都换算成 UNC）；两台各自映射不同共享名但实为同一目录、或本地盘经别名共享出去的，无法识别。
旧小助手（< 1.3.19）不报网络路径与状态：跨电脑比较跳过、状态不显示，其余照旧。

## 验证

- 本地闸门（development.yml 同名步骤）0 失败；`check-i18n --strict`、`i18n-build --check` 通过。
- Cowork 全量 pytest 与 main 失败集合相同（无新增失败）。
- 小助手测试 609 通过（含新增 share_path / 文件夹状态 / 同步载荷）。
- 测试机生产镜像（`/opt/pearnly-testenv/env` 的 PEARNLY_IMAGE）挂候选源码：`test_cowork_folder_intake` 21、`express_choices` 10、
  `express_preflight`、`express_posting_wire`、`mrerp_push`、`push_account` / `contract` / `lifecycle` / `native_contract` / `purity` 全过。
- 未做：真机两台电脑同共享的端到端试用；服务端改动随下一批生产部署上线（小助手 1.3.19 合入后发正式版）。
