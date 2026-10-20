# fatcc.com 登录：浏览器「保存密码」认出用户名（问题清单第 11 条）＋ 退出不出确认页

- 状态：本次范围完成，待合入与部署（本窗口不合并 main、不部署）
- 分支：`pearnly/fatcc-login-autocomplete`，基线 `origin/main` = `241b9ef8`
- 来源：`~/Downloads/pearnly-cc-video/功能分集/问题清单-给修复窗口.md` 第 11 条——Chrome 登录 fatcc.com 后「保存密码」框里用户名是空的
- 授权范围：登录页 10-17 起冻结（两套 Keycloak 登录主题的文案、布局、动画不改）；用户这次只批准补表单属性。本次没有改任何可见文字、样式、脚本和动画
- 追加（开发窗口转达、用户已同意）：任何产品退出都不出现身份服务的「您确定要注销吗？」确认页，点退出直接退出并跳回。见下方「退出不出确认页」一节

## 线上核实（10-20，只读，没有输入真实账号、没有提交表单）

| 步骤 | 页面 | 线上 HTML（摘录） | 结论 |
| --- | --- | --- | --- |
| 第一步 | `https://fatcc.com/`（计费服务上的入口站点，`apps/pearnly_control/.../www/ai.html`，由 `scripts/build-ai-entry.py` 生成） | `<input ... id="login" name="login" autocomplete="username" ...>`、`<input id="password" name="password" type="password" autocomplete="current-password" ...>` | 属性本来就对，与仓库一致 |
| 第二步 | `https://auth.pearnly.com/realms/pearnly-business/protocol/openid-connect/auth?client_id=pearnly-ai&...&login_hint=<假账号>`（Keycloak 主题 `pearnly-ai`） | `<input type="hidden" id="username" name="username" value="<login_hint>" />`；密码框 `autocomplete="current-password"`；主题资源 `ai.e9fefff2b5.js` / `ai.5bf66af14c.css` | **账号是 `type="hidden"`** |

原始抓取存 `~/pearnly-devtools/fatcc-autocomplete/step1-live.html`、`step2-live.html`（第二步用的是不存在的假账号 `autocomplete-check`，GET 请求只建了一个未用的登录会话）。

## 根因

两步跨域：第一步在 fatcc.com 只问账号所在账号池，然后跳到 auth.pearnly.com，密码放在 `#pn=` 里由第二页脚本自动提交（`kc-form-login`）。
浏览器按**最后提交密码的那个表单**（auth.pearnly.com）提示保存，并且只从同一表单里的文本框找账号；`type="hidden"` 的输入框不参与密码管理器识别，所以保存框里用户名为空。第一步的属性再完整也关联不过去（不同域名、不同表单）。

同类缺陷（同一套机制）：

- `pearnly` 主题 `login.ftl` 重新验证（`usernameHidden`）时整个账号框不渲染，表单里没有账号。
- `pearnly` 主题 `login-update-password.ftl`（设新密码；`pearnly-ai` 继承它）表单里没有账号，浏览器不知道新密码属于谁。
- `pearnly` 主题 `login-reset-password.ftl`（忘记密码；`pearnly-ai` 继承它）账号框缺 `autocomplete="username"`。
- 设新密码两个框本来就有 `autocomplete="new-password"`，不用改。

## 修法（只补属性 / 不显示的字段）

| 文件 | 改动 |
| --- | --- |
| `scripts/build-ai-entry.py` → 生成 `infrastructure/keycloak/themes/pearnly-ai/login/login.ftl` | 第二步账号由 `type="hidden"` 改为 `type="text" ... autocomplete="username" readonly style="display:none"`（name/id/value 不变，照常提交；「更换」按钮、`#pn=` 自动提交逻辑都按 `id="username"` 找，不受影响）。JS/CSS 没变，资源指纹文件名不变 |
| `infrastructure/keycloak/themes/pearnly/login/login.ftl` | `usernameHidden` 且 `auth.attemptedUsername` 有值时，补同样的不显示只读账号框 |
| `infrastructure/keycloak/themes/pearnly/login/login-update-password.ftl` | 表单开头补不显示只读账号框，值取 Keycloak 给这页的 `username` 变量（本地实测这页 `auth.attemptedUsername` 为空、`username` 有值） |
| `infrastructure/keycloak/themes/pearnly/login/login-reset-password.ftl` | 账号框补 `autocomplete="username"` |
| `tests/test_assistant_entry.py` | 新增 `test_password_forms_name_the_account_for_password_managers`：五个表单的账号框都带 `autocomplete="username"`、都不是 `type="hidden"`，密码框 `current-password` / `new-password` 正确，第二步账号框保持不显示 |

为什么用 `readonly` + `display:none`：不显示保证布局零变化；只读防止浏览器自动填充把入口带过来的账号换成另一个已存的账号。这是 Keycloak 早期官方主题在设新密码页给密码管理器用的同一写法。

## 部署步骤（给开发窗口）

- 这一节的改动只在 Keycloak 主题（宿主机挂载，`infrastructure/production/compose.yml` 的 keycloak `volumes`）和测试；入口站点 `ai.html` 重新生成后内容不变，计费服务没有实质变化。
- 走常规生产流程即可（见记忆 production-deploy-steps）：合入 main → 从 main 构建镜像 → stage（部署拷贝同步，`check-deploy-image.py` 会核对 `infrastructure/keycloak/themes` 与镜像副本一致）→ **`scripts/production/activate-foundation.sh`**。
  - `activate-foundation.sh` 会算主题目录摘要，和 `/opt/pearnly/state/keycloak-themes.sha256` 不同且 Keycloak 容器没重建时，自动清 `/opt/keycloak/data/tmp/kc-gzip-cache` 并 **重启 Keycloak（停登约 40 秒）**。不需要手动重启，也不需要另外清浏览器缓存（模板是服务端渲染，JS/CSS 文件名没变）。
  - 只激活某个产品（activate-product）不会让 Keycloak 换主题；这批要包含 activate-foundation。
- 测试环境（testenv）若想先试：Keycloak 主题同样是挂载，按 `docs/development/test-environment.md` 的主题挂载方式重载即可。
- 上线后回读：用不存在的假账号 GET 第二步页面（同上表 URL），确认 `id="username"` 那一行是 `type="text" ... autocomplete="username" readonly style="display:none"`。

## 验证

| 层级 | 环境 / 版本 | 场景 | 结果 | 证据 |
| --- | --- | --- | --- | --- |
| 本地实现 | 工作树（本分支），一次性 Keycloak 26.7.4 容器 `fatcc-autocomplete-kc`（与兼容矩阵同版本、同 digest），挂本工作树两套主题，临时测试 realm（两个测试账号，密码为本次随机生成，只存本机） | 第二步（带 login_hint / 不带）、忘记密码（pearnly-ai 与 pearnly 各一）、pearnly 登录、pearnly 重新验证、设新密码，共 7 页 | 账号框属性如上；登录卡片内每个可见元素的位置和尺寸改前改后**完全一致**（7 页逐元素对比 `same: true`，ai 两页等入场动画结束后量） | 内置浏览器量测；截图 `~/pearnly-devtools/fatcc-autocomplete/pane-before-*.jpg`、`pane-after-*.jpg` |
| 本地像素 | 同上，无界面 Chrome、减少动效、1280×900，三种语言 × 5 页 | 改前 vs 改后 | ai 主题：0 像素差或只有右上角语言切换处 53 像素（改前自己连拍两次也有同样 53 像素抖动）；pearnly 主题：差异只在左侧动画插画区，量级与改前连拍的抖动相同 | `~/pearnly-devtools/fatcc-autocomplete/before/`、`before-repeat/`、`after/`，`pixdiff.sh` 对比脚本 |
| 本地行为 | 同上 | 第二步「更换」→ 变成可编辑账号框并聚焦；模拟入口带 `#pn=` 自动提交 → 拿到授权码；重新验证页带账号字段提交 → 拿到授权码；设新密码页带账号字段提交 → 拿到授权码 | 通过 | 内置浏览器 |
| 单元测试 | 本分支 | `python3 tests/test_assistant_entry.py` | 26 项通过（含下一节新增的一项） | — |
| 本地闸门 | 本分支（含下一节退出改动） | `.github/workflows/development.yml` 全部 run 步骤（`~/pearnly-devtools/gate.sh`） | 见文末「闸门与 CI」 | — |
| 浏览器保存框 | — | Chrome 真正弹出的「保存密码」框是否带用户名 | **未验**：需要真实账号登录线上；部署后请用户用 Chrome 登录一次 fatcc.com 确认 | — |
| 生产 | — | — | 未部署 | — |

## 限制

- Chrome 会把密码记在 **auth.pearnly.com** 名下（密码在那一页提交）。下次在 fatcc.com 第一页，Chrome 不一定会自动填（不同域名）；要让 fatcc.com 也能自动填，需要改登录结构（例如密码只在一个域名输入），属于登录页改动，超出本次「只补属性」的授权，未做。
- 本地只验证了表单结构与 Keycloak 提交行为；Chrome 密码管理器的最终行为以上线后真机为准。

## 退出不出确认页（10-20 追加）

### 现象与查明的原因

用户在本地预览里看到身份服务的退出确认页（「注销 / 您确定要注销吗？ [注销]」裸按钮 + 底部「登录即表示…」）。本地 Keycloak 26.7.4 实测：

| 退出地址 | 浏览器里还有身份服务会话 | 结果（改前） |
| --- | --- | --- |
| 带有效 `id_token_hint` + 跳回地址 | 有 | 直接退出并 302 跳回 |
| 带**过期**的 `id_token_hint`（过期 26 秒） | 有 | 照样直接退出并跳回（身份服务不查提示有效期） |
| 带提示，但对应会话已不在 | 无 | 直接跳回 |
| 带**别的 realm** 签发的提示 | — | 400 错误页，退不出去 |
| **不带提示** + 跳回地址 | 有 | **确认页** |
| 不带提示 + 跳回地址 | 无 | 直接跳回 |
| 不带提示、不带跳回地址（只有 `client_id` 或什么都没有） | 有 | 确认页，点完后「您已被登出」卡片停在那里 |

产品里所有退出入口都走 `pearnly_core.identity.sign_out`（Desk `public/desk/product.js`、`frontend/src/components/AccountMenu.vue`、CC 对话页 `frontend/assistant/src/assistant.tsx`）；LINE 登录被拒时的「退出重试」走 `account/line_login.py::end_session_url`，用当次回调刚拿到的 id_token，必有效。控制面、各产品没有别的身份服务退出入口（`git grep end_session / openid-connect/logout / sign_out`）。

`sign_out` 掉到确认页只有「取不到提示」一种情况。查下去发现**会话里那份提示从来没存住**：`oidc_session.remember` 写的是 `frappe.session[...]`（会话顶层），而 Frappe 16.34.1 只把 `frappe.session.data` 写进 Sessions 表，下次请求 `resume()` 也只恢复 `data` 这一层（`frappe/sessions.py`：`insert_session_record`、`update` 只存 `self.data["data"]`，`get_session_data_from_cache` 返回 `data.data`）。所以实际只靠状态 Redis（redis-queue）那份，它一丢（重建、清理、过期）点退出就出确认页。另外换过账号池的旧会话，提示是旧 realm 签发的，身份服务直接出错误页。

### 修法

| 文件 | 改动 |
| --- | --- |
| `apps/pearnly_core/pearnly_core/account/oidc_session.py` | 提示改存 `frappe.session.data`；登录这次请求里会话记录已先插入，所以值变了时 `frappe.local.session_obj.update(force=True)` 写回一次（与 Frappe 自己存 CSRF 令牌同一做法）。`logout_hint` 先读会话那份、再读 Redis；`forget` 两份都清 |
| `apps/pearnly_core/pearnly_core/identity.py` | `sign_out` 只带身份服务会接受的提示（`_usable_logout_hint`：`iss` 是本站当前 realm、`aud`/`azp` 含本站客户端；不验签，由身份服务验）。不合格就不带，交给下面的自动确认 |
| `infrastructure/keycloak/themes/pearnly/login/logout-confirm.ftl`（新）、`scripts/build-ai-entry.py` → `pearnly-ai/login/logout-confirm.ftl`（新） | 兜底：确认页不套登录外壳、不显示任何内容，底色用各自登录页样式表；加载即 `form.submit()` 提交身份服务给的确认表单（`action` 用 `url.logoutConfirmAction`，`session_code` 原样带回）；`<noscript>` 里留原生提交按钮（文字用身份服务标准键 `doLogout`，没有新文案） |
| `infrastructure/keycloak/themes/pearnly/login/info.ftl`（`pearnly-ai` 继承） | 只针对「退出成功」这张提示页（`message.type == success` 且内容等于 `successLogout`）：有跳回地址（`pageRedirectUri`）或产品客户端首页（`client.baseUrl`；身份服务自带的 account 等客户端除外）就输出不显示内容的过渡页，`meta refresh` 立即跳走；其余提示页不变 |
| 两套 `theme.properties` | 新增 `pearnlyBlankCss`（过渡页底色用的样式表；ai 主题由生成器写入带指纹的文件名）；ai 主题加 `pearnlyBlankTitle=CC` |
| 测试 | `apps/pearnly_core/pearnly_core/account/test_oidc_session_hint.py`（新，已加入 `scripts/ci-candidate-checks.sh` 离线组）；`test_preferences.py` 一处替身补 `session.get`（这条原用例在旧写法下也走不到 Redis 分支：MagicMock 恒为真）；`tests/test_assistant_entry.py::test_logout_confirmation_submits_itself_without_a_card` |

登录页本身（login.ftl 的布局、文案、动画）没有因为这一节再改。

### 验证（本地，同一个 Keycloak 26.7.4 一次性容器）

| 场景 | 改前 | 改后 | 证据 |
| --- | --- | --- | --- |
| 有会话、不带提示 + 跳回地址（CC 主题） | 确认页 | 直接到跳回地址；再发登录请求出登录页（会话确已结束） | 内置浏览器；改前 `logout-before-ai-confirm.jpg` |
| 同上（pearnly 主题） | 确认页 | 直接跳回 | 改前 `logout-before-pearnly-confirm.jpg` |
| 有会话、只带 `client_id=pearnly-ai`（客户端首页按生产 `configure-ai-entry.py` 设成入口地址） | 确认页 →「您已被登出」卡片 | 直接到客户端首页 | 改前 `logout-before-ai-info-page.jpg` |
| 有效提示 / 过期提示 / 会话已不在的提示 | 直接跳回 | 照旧直接跳回 | 内置浏览器 |
| 不带任何参数（客户端是身份服务自带的 account） | 确认页 → 卡片 | 自动确认后仍停在「您已被登出」卡片：没有产品可跳，产品入口也不会走到这里 | 内置浏览器 |
| 过渡页本身长什么样（同一份 HTML 去掉自动提交脚本后渲染） | — | 只有底色：CC 主题 `#f8f8f6`，pearnly 主题登录页渐变；没有文字、没有卡片 | `logout-after-ai-confirm-page-without-autosubmit.jpg`、`logout-after-pearnly-confirm-page-without-autosubmit.jpg` |
| 提示存放与筛选（`pearnly/frappe-sample:16.34.1` 镜像里跑，挂本工作树 `pearnly_core`） | — | `test_oidc_session_hint`（10 项）+ `test_identity_callback` + `test_preferences` 共 34 项通过 | — |

截图在 `~/pearnly-devtools/fatcc-autocomplete/`；`logout_probe.py` 是不带浏览器会话的退出探针。

未验：真实 Frappe 站点上「登录 → 清掉状态 Redis 那份 → 点退出」的端到端（要测试环境）；`aud` 不符的提示会被身份服务拒绝是按其规则推定，本地 realm 没开密码直取，没构造出这种令牌。

### 部署

- 主题部分同上一节：随 `activate-foundation.sh`，主题摘要变了会自动清压缩缓存并重启 Keycloak（停登约 40 秒），不用手动；`theme.properties` 的改动在同一次重启里生效。
- `pearnly_core` 改了（`oidc_session.py`、`identity.py`），要随产品镜像发到 ERP / Cowork / DMS（activate-product），不是只换主题。按「共用层改动要跑候选离线检查」，合入前在生产镜像跑一次完整 `IMAGE=<候选> scripts/ci-candidate-checks.sh offline`（分支 CI 不跑这一组）。
- 上线时已登录的旧会话：会话里还没有那份提示，Redis 那份在就照常带上；都没有时走主题自动确认，用户同样看不到确认页。新登录的会话两份都有。

## 收尾

- 一次性 Keycloak 容器 `fatcc-autocomplete-kc` 已删除；临时测试 realm（含随机生成的测试密码）与临时脚本已删除。截图和对比脚本留在 `~/pearnly-devtools/fatcc-autocomplete/`（`capture.sh` / `pixdiff.sh` 依赖那个容器，要重跑先按本记录重建）。
- 工作树与分支：分支推送待合入（本窗口不合并、不部署）；工作树保留到合入后由合入窗口清理。

## 闸门与 CI

- 本地：`.github/workflows/development.yml` 全部 run 步骤 110 项通过、0 失败（`~/pearnly-devtools/gate.sh`，含 `check-architecture.py`、`check-copy.py`：新增 / 改动文案 0）。
- 分支 CI（Development checks）：推送后以 GitHub 上该分支的运行结果为准。
- 生产镜像候选离线组（`scripts/ci-candidate-checks.sh offline`）：未跑，合入前由合入窗口跑。
