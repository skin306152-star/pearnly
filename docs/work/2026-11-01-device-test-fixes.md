# 11-01 部署后真机实测小问题修复

> 来源：`docs/work/2026-11-01-post-deploy-device-tests.md`（分支 pearnly/post-deploy-device-tests-b38efd）记下的小问题。
> 分支 `pearnly/device-test-fixes-3bb5be`，基线 `5d67b418`（= origin/main，生产 62a752ce 之后的 main）。不部署。

## D1 DMS 身份证取景页加载时露词条键

- 现象：LINE「扫描身份证」打开后约 8 秒，正文和按钮显示 `dms.edit.loading`、`dms.edit.scanBack`。
- 根因：LINE 内 SPA 页（`frontend/src/pages/dms/ScanPage.vue`、`EditorPage.vue` 等）先渲染、`onMounted` 才去取词条；
  取词器 `frontend/src/lib/i18n.ts` 的 `t()` 缺词时回键名（设计上是「缺译看得见」），取回之前所有词都算缺。
  服务端 `browser_text` 本身 0.2–0.3 秒（本机直连生产 testdms 实测，`dms.` 整包 gzip 39KB），8 秒是手机弱网（-84 dBm）。
- 修：取词器加「取过一次」标记，没取回前 `t()` 回空串（页面只剩转圈），取过之后缺的键照旧显示键名；
  取景页「返回聊天」按钮等词条到了再画（空按钮不画）；取景页只取 `dms.edit.` 这一段（gzip 7KB）。
  同一取词器的其他 SPA 页（编辑页、复核页、账户页、CC 连接页、Cowork 录入、采购页、侧栏菜单）一并不再露键名。

## D2 订车单编辑保存后预览卡地址丢前缀

- 现象：首张汇总卡「12/7 หมู่ 5 ซ.ลาดพร้าว 71 ต.ลาดพร้าว อ.ลาดพร้าว จ.กรุงเทพมหานคร 10230」，编辑只改交车日期保存后
  变成「12/7 5 ลาดพร้าว 71 ลาดพร้าว ลาดพร้าว กรุงเทพมหานคร 10230」。
- 根因：编辑保存（`editor/booking_edit.py`）用 `qa_cards._fallback_summary` 重算卡片汇总，它的 `_address_line` 只把各段空格相连；
  首张卡走 `intake/draft.py` 的 `master_snapshot` / `build_summary`，各自再拼一遍、带前缀。三处三套拼法。
- 修：`intake/draft.py` 新增 `address_line`（门牌、楼、村、หมู่、ซ.、ถ.、ต.、อ.、จ.、邮编），`build_summary`、`master_snapshot`、
  `_fallback_summary` 都用它，删掉 `qa_cards._address_line`。只影响卡片显示，写入 DMS 的字段不变。
  有意变化：首张卡（新建 / 保留旧资料）现在也显示楼名、村名（原来那两处漏了，编辑后那张原本就有）。
- 证据：`tests/test_dms_card_address_line.py`（零 IO，已进 `check-dms-offline.py`）；在基线代码上跑同一测试得到真机那串无前缀地址，新代码通过。

## 3 订车单被拒时软键盘盖住交车日期栏

- 现象：越界交车日期保存被拒，页面滚到那一栏、光标进栏、软键盘弹出，那一栏正好被键盘和贴底操作条盖住。
- 根因：`EditorPage.vue` 的 `showInvalid` 先按整个视口居中再聚焦；键盘弹出后可见区变矮，浏览器只把输入框挪到可见区最底下，
  被贴底（sticky）的操作条（错误提示 + 保存）盖住。
- 修：聚焦后 1.5 秒内监听 `visualViewport` 的变化，把这一栏摆到「可见区减去操作条」的中间；已在可见区内不动。

## 4 团队页批量停用后不刷新（ERP、Cowork 同一处）

- 现象：testcowork 勾一位员工点「停用 1」，按钮一直灰、没有成功提示，列表 20 多秒不变，手动刷新才显示已停用。
- 根因（列表不刷新）：两个团队页的名单是一次取全放在页面里的（`data.members`，`load` 只在本地排序）；批量操作走共享的
  `p.selection_action`，做完调 `list.reload()`，但没人重新取名单，所以重载出来还是旧状态。单个员工弹窗里的停用是先
  `refresh_members()` 再重载，所以那条路正常。
- 修：两个 `team.js` 的批量 `run` 做完先 `await refresh_members()`。只动批量操作这一段，不碰表单布局窗口在改的字段顺序。
- 证据：流程 `erp.team.bulk-disable`、`cowork.team.bulk-disable`（`scripts/flows/team_bulk_disable.py`）：勾选 → 批量按钮 →
  原生成功提示、按钮回到「新建」可点、这一行不刷新页面就显示已停用、库里 `enabled = 0`。结果见下方「验证」。
- 顺带发现（未改，交主会话）：停用员工时框架 `LoginManager.logout(user=…)` 会跑 `on_logout` 钩子，
  `pearnly_core.assistant.push.forget_session` 按**当前会话**（管理员自己）删推送订阅——管理员停用别人，自己这台设备的 CC 推送订阅被删。
  实验栈实测：给 admin@cowork.regression 当前会话插一条订阅，用这个会话停用一位新员工（200），这条订阅 1 → 0。已报主会话另派。

## 5 LIFF 首次打开白页约 25 秒

生产 nginx 访问日志（主会话批准，三次只读 SSH）里三星那次富菜单「盘点」（UTC 11-01 05:41）：

| 时刻 | 请求 | 间隔 |
| --- | --- | --- |
| :08 | testerp 入口（LINE 普通内置浏览器）→ 302 liff.line.me | |
| :09 | LIFF 打开站点 → 入口 → 302 身份服务 | 1 秒 |
| :10 | 身份服务 auth、broker → 303 access.line.me | 1 秒 |
| :21 | LINE 回调身份服务 broker/endpoint → 302 | **11 秒** |
| :35 | 站点 identity.callback 完成 → 302 盘点页 | **14 秒** |
| :36 | 盘点页 HTML 与 40 个脚本 | 1 秒 |
| :38 | 盘点列表数据 | 2 秒 |

- 服务器端可测的每一跳都快：web 容器到 auth.pearnly.com 的 discovery / JWKS / token 0.11–0.27 秒（Python requests 同一路径）；
  Keycloak 网络到 api.line.me 0.38 秒、access.line.me 0.82 秒；IPv6 无路由但立即失败（≤0.15 秒），不拖时间；静态资源 gzip + 一年缓存。
- 当时 Keycloak 的 `user_not_found`（05:41:20）、`expired_code`（05:42:00）来自同一出口 IP 下一台 iPhone 在密码登录页的提交，与这次 LIFF 无关；
  测试账号的 LINE 绑定 active / linked，这次 LINE 登录成功。
- 判断：第一段 11 秒基本在 LINE 那边（LIFF 里 access.line.me 自动登录 + 弱网）；第二段 14 秒，访问日志只有秒级、没有 request_time，
  分不清是手机发回调慢还是回调本身慢。
- 主会话定：(a) 做——`identity.callback` 每次打一行分段计时（`{"event":"login_callback",…,"steps":{state,token,verify,resolve,session,profile,landing}}`，
  毫秒，容器标准输出，不进 Error Log，不带账号 / 令牌 / 身份标识），随下一批部署后用三星真机首开一次拿数据；
  (b) 富菜单直接写 liff.line.me 省约 1 秒，要重发所有官方号富菜单，先不做；
  (c) LIFF 里用 `liff.getIDToken()` 直接建会话（跳过 access.line.me 与身份服务两段），改了「会话由身份服务签发」的设计，等 (a) 的数据再问用户。

## 验证

实验栈 `pearnly-devfix`（本工作树源码，镜像 = 回归实验室默认 digest），三星 SM-A146P 手机 Chrome 经 adb reverse 开实验栈页面（已在共享占用表登记并释放）。

| 项 | 改前（同一环境对照） | 改后 |
| --- | --- | --- |
| D1 | 手机 Chrome 限速（1.5 秒往返、40KB/s）开 `#/dms/scan`，每 0.3 秒取页面文字：17 次露出 `dms.edit.loading`、`dms.edit.scanBack`（同真机记录）；新流程 `dms.liff.scan-loading` 在旧代码上同样抓到这两个键、不通过 | 同样限速 25 秒：0 次露键名，最后显示「正在加载资料… / 返回聊天」；`dms.liff.scan-loading` 通过（56 次采样 0 个键名，按钮与取景提示是正式文字） |
| D2 | `tests/test_dms_card_address_line.py` 在基线代码上得到「12/7 5 ลาดพร้าว 71 ลาดพร้าว ลาดพร้าว กรุงเทพมหานคร 10230」 | 测试 3/3 通过；流程 `dms.booking.editor` 加断言「编辑保存后的预览卡地址带 หมู่ / ซ. / จ.」，new/old × desktop/mobile 4/4 通过 |
| 3 键盘 | 手机 Chrome 真触摸点保存（CDP 触摸事件），两种键盘模式（默认只缩可见区；`interactive-widget=resizes-content` 模拟整页变矮）出错栏都在可见区——Chrome 里复现不出 LINE 的遮挡 | 两种模式下出错栏都在键盘和贴底提示条上方、光标在栏内，无退化。**LINE 内置浏览器里的遮挡没在本机复现、修后没在 LINE 里验**，要随部署后用 LINE 真机看 |
| 4 团队页 | 流程 `cowork.team.bulk-disable`（改前 team.js）：成功提示有，但「列表当场显示已停用」不通过 | `cowork.team.bulk-disable` 2/2、`erp.team.bulk-disable` 4/4 通过：成功提示约 0.25 秒出现、按钮回到「新建」可点、行不刷新就显示已停用、库里 `enabled=0` |
| 4 慢 | — | 「按钮灰 20 多秒、无提示」实验栈复现不出：停用接口 0.25 秒；被停用的人有活会话时 0.62 秒，且那人会话随即 403。服务器端不慢；生产那次只有秒级访问日志，未再查 |
| 5 计时 | — | `test_identity_callback.py` 7/7（新增「每次回调一行计时、不含账号与身份标识」），实验室 bench 容器里跑 |

- 新流程 `dms.liff.scan-loading`：流程驱动加了通用的 `PRELOAD`（`ui_steps.mjs` 的 `preload`，CDP `Page.addScriptToEvaluateOnNewDocument`，页面脚本跑之前注入），
  这条流程用它把 `browser_text` 放慢 2.5 秒、每 50 毫秒采样页面文字。`frontend/src/lib/i18n.ts` 同时登记进 `dms.booking.editor` 的 covers。
- 变基到 `760e64a9` 后 `run.py --changed --base origin/main`：7 个流程 21/21 格通过。
- 候选离线检查：生产当前镜像（`fd60caff…`）挂本工作树五个 App 跑 `scripts/ci-candidate-checks.sh offline`，90 项全过（EXIT=0）。
- 全量闸门：`.github/workflows/development.yml` 全部 156 项本地通过（改动后重跑，结果见下）。
- 共用件连带回归（`scripts/check-impact.py`）：命中 `dms-id-scan`（dms.liff.id-scan：手机 Chrome 实测取景页加载，LINE 内拍照上传没测到，要部署后真机）、
  `login-session`（identity.callback 只加计时，登录流程不变；`shared.login` / `shared.line.login` 等手动入口没测到，靠单元测试，部署后三星真机首开一次即覆盖 LINE 登录）、
  `erp.page.team` / `cowork.page.team`（团队页流程 `*.team.full-name`、`erp.team.add-member`、`*.team.bulk-disable` 实验栈通过）、`cc.browser-text`（SPA 取词器：编辑页、取景页实测；其余 SPA 页同一取词器、行为只在取回前不同，没逐页测）。
- 文案：`check-copy.py` 新增 0、改动 0。

## 后续：停用员工删了管理员自己的推送订阅（主会话 11-01 派，分支 `pearnly/logout-push-subscription`）

- 现象（实验栈实测）：管理员当前登录有一条 CC 推送订阅，停用一位员工（200）后这条订阅 1 → 0。
- 根因：框架只在两处「替别人登出」——`User.check_enable_disable`（停用）和 `User.on_trash`（删除），都调
  `LoginManager.logout(user=那个人)`；它先跑 `on_logout` 钩子，钩子只拿到 `login_manager`，不知道登出的是谁。
  `pearnly_core.assistant.push.forget_session` 一律按当前会话（管理员）删，于是删了管理员的、被停用人的反而留着（发出时才因会话已不在被清）。
  全仓 `on_logout` / `on_session_creation` 钩子只有这一个 `on_logout` 和 `sessions.register_session`（只在本人登录建会话时记账，不受影响）。
- 修：`forget_session` 沿调用栈找到 `LoginManager.logout` 那一帧（按函数代码对象认），读它的 `user`。是别人 → 删那个人的全部订阅、不动当前会话的；
  空或是自己 → 照旧只删本次登录的；找不到这一帧 → 按本人。两处调用点用同一办法覆盖，不用各自打标记（删除没有能抢在框架之前的钩子）。
- 证据：`tests/test_assistant_push.py` 新增「替别人登出保留我的、删他的」（与框架同签名的替身），生产镜像里 39/39 通过；
  实验栈真框架：停用员工后管理员这次登录的订阅 1（改前 0）、被停用人的 0；管理员本人退出后自己的 0。删除用户那条调用点没单独实测（同一个 `logout(user=…)`）。
