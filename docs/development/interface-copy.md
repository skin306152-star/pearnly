# 界面文案

界面文案只写完成操作必需的字：官方、简短、陈述事实。不写原因、过程、提示、欢迎语和开发说明，不用口语；Windows 端在用户可见处只叫「CC 桌面版」。想加说明时，先考虑能否用状态、图标、禁用态或官方组件自带文案表达；能沿用 Frappe / ERPNext 自带说法的就沿用。

`scripts/check-i18n.py` 管"中文 / 泰文不许写死、必须进字典"；`scripts/check-copy.py` 管"进了字典的字是不是碎碎念"，以及新代码有没有绕开字典（写死英文、拼接、动态 msgid）。两道闸都在 Development checks 里。

## 怎么用

```bash
python3 scripts/check-copy.py                 # 差异模式：相对 merge-base(origin/main, HEAD)，当前 = 工作树（含未提交）
python3 scripts/check-copy.py --report        # 同时输出 Markdown 清单（交付时贴给用户和测试窗口）
python3 scripts/check-copy.py --report --out /tmp/copy.md
python3 scripts/check-copy.py --repo <另一个工作树> --base origin/main   # 只读地看别的分支
python3 scripts/check-copy.py --audit         # 全量审计存量（只报告，退出码 0；--all 同义；--strict 时有拦截退出码 1）
python3 scripts/check-copy.py --unused-keys   # 术语表里代码没引用的键（参考）
python3 tests/test_check_copy.py              # 闸门自己的测试（改词表、改规则时必跑）
```

退出码：差异模式有拦截 = 1，取不到基线 = 2。`--strict` 只影响 `--audit`，差异模式本来就拦。

基线：环境变量 `COPY_GATE_BASE` 与 HEAD 的 merge-base（CI：PR 用 `HEAD^1`，即 GitHub 实际合并到的 base；推 main 用 `event.before`）；没有就用 `origin/main`；都取不到判失败，不跳过。浅克隆取不到时会明确提示 `fetch-depth: 0` / `git fetch --unshallow`。本地先 `git fetch origin main`，`origin/main` 旧了只会让检查范围更宽，不会漏。

接入点：

- CI（`.github/workflows/development.yml`，清单写进 Step Summary）。分支 CI 看整个分支相对 merge-base 的累计改动。
- 共享提交钩子：`--staged --tree <暂存快照>`，基线取 HEAD，只拦**本次提交**新加的问题 —— 在途分支早先提交留下的问题不会卡住与文案无关的提交（那些由分支 CI 拦）；别的提交留下的失效豁免在钩子里只标黄。分支还没变基到含闸门的 main 时提示后跳过。合并提交（把 main 并进任务分支、解决冲突后提交，`MERGE_HEAD` 在）只判合并结果里有、而第一父提交与合并进来的父提交里都没有的行（main 已有的行不当成本分支新增；解决冲突时手工新写的照拦）；词表里 main 自己删的行也不算这次放宽。
- `~/pearnly-devtools/gate.sh`（按 workflow 的 run 行执行，不用改）。

扫描范围按 `git ls-files -co --exclude-standard` 取文件：`.gitignore` 里的构建产物（CC 网页打包后的 JS）不扫，CI、本地、钩子看到的范围一致。

## 扫哪些来源

| 来源 | 词条 id（`来源:键`） | 判什么 |
| --- | --- | --- |
| 术语表 `apps/<app>/<app>/i18n/messages.tsv` | `<app>:<key>`，无 key 用 en（msgid） | 内容判据，四种语言 |
| CC 网页 `frontend/assistant/src/i18n.ts` | `assistant:<对象>.<键路径>`（按键比对，一行拆多行不算改动） | 内容判据 |
| CC 桌面版 `companion/src/companion/i18n.py` 的 `_M` | `companion:<key>`（中文为准） | 内容判据 |
| Keycloak 主题 `messages_*.properties` | `keycloak:<主题>.<key>` | 内容判据 |
| keep-cjk 整文件豁免的四语表（`sources.TABLE_MODULES`） | `table:<模块>#<路径>` | 内容判据 |
| Python（改动文件）：`_()`；`frappe.throw / msgprint`（位置参数与 `msg=` / `title=`，含 f-string）、`publish_realtime("msgprint", …)`、`frappe.*Error(…)` 的写死文案（只作重试信号、不到界面的 `QueryDeadlockError` / `QueryTimeoutError` 不判）；`_()` 与字符串或另一个 `_()` 用 `+` 拼接；f-string / `+` / `.format` 拼出来的 msgid；assistant 工具声明的 label / ask / ask_when、`*STEP_NAMES` | — | R-HARD / R-CONCAT |
| Desk JS（改动文件）：`__("…")`、`t("键")`；`frappe.msgprint / throw / confirm / show_alert / prompt`、`set_intro / set_headline` 的写死首参（含 `{message: "…"}`）；`__()` 与字面量或另一个 `__()` 拼接；模板串拼出来的 msgid | — | R-HARD / R-CONCAT |
| CC 网页 TSX / TS（改动文件）：JSX 文本与 `{"…"}` 子节点；title / aria-label / placeholder / alt / label / content / description / message / tooltip / helperText 属性（含 `` {`…`} `` 与 `{cond ? "a" : "b"}`）；`toast.*()` / `alert()` / `confirm()` 的字面量；`tr()`、`t()`；相邻 `{t(…)}{t(…)}`、模板串里 ≥ 2 个 `t(` / `tr(` 插值 | — | R-HARD / R-CONCAT |
| CC 桌面版程序 `companion/src/companion/*.py`（`i18n.py` 除外，改动文件）：Qt 控件与通知（`QLabel` / `setText` / `setWindowTitle` / `setToolTip` / `addAction` / `showMessage` / `_notify` / `QMessageBox.*` …）、`{"message": "…"}` 的写死文案；`t("键")` 的键不在 `_M` 里 | — | R-HARD |
| DocType / Workspace / Report JSON（改动文件）：新写的 label / description 必须在术语表或 Frappe 自带词里（内容由术语表那一行判）；Select 选项是取值码，不要求；只在 hidden 字段上的（Desk 表单不显示）不在字典里只标黄 | — | R-HARD |
| `www/*.html`、`templates/emails/*.html`（改动文件）：写死中文 / 泰文；标签外 ≥ 2 个英文词的文本与 title / placeholder / alt / aria-label；页内 `<script>`（不带 src）的字符串字面量：中文 / 泰文一律算，英文句子写进 `textContent` / `innerHTML` / `innerText` 或交给 `msgprint` / `alert` / `confirm` 时算（`{{ _("…") \| tojson }}`、取词函数的键不算） | — | R-HARD；只被邮件模板引用的词条按「邮件」角色判 |
| 翻译包 `translations/*.csv` 新增行（术语表里没有的 msgid） | — | 标黄（手工行或改写残留）；新代码不能拿它兜底 |

"在不在字典里"认：当前术语表 + 基线翻译包 + 锁定镜像里 Frappe / ERPNext 自带的 msgid（`scripts/i18n/upstream-msgids.txt`，从 `infrastructure/images/frappe.Dockerfile` 的 `FRAPPE_BASE` 镜像导出的 `main.pot`，升级底座时一起重新导出）。Frappe 自带词只给 `_()` / `__()` / 页面 / DocType 用；`t("键")` / 工具声明走本仓术语表，不认它；CC 网页的 `tr()` 只认 `folder_intake/api.py` 的 `TERM_WORDS` 里直接列出的 Frappe 自带 msgid（`terms()` 按当前语言用 Frappe 官方译文给出），没列进去的照拦。术语表里无键行的英文就是 Frappe / ERPNext 自带 msgid 的，英文不归本仓、不判（中 / 泰 / 日译文照判）。按键族取词（`_(f"cowork.work.status.{status}")`、`'erp.detail.' + code`）拼的是语义键，不算动态 msgid。

不扫：给模型的提示词与工具说明（`assistant/definition.py`、OCR 与对账的 prompts / fewshot / schema）、写进 Express / MR.ERP / DMS 的业务数据、日志与断言、`tests/`、`website/`。清单在 `scripts/copygate/sources.py` 的 `NOT_SCANNED`，每项写原因。keep-cjk 再加"规格："整文件豁免时，要在 `TABLE_MODULES`（给用户看的四语表）或 `NOT_COPY_SPEC`（不是界面文案）登记，`tests/test_check_copy.py` 守着。

## 分类

| 类别 | 条件 | 处理 |
| --- | --- | --- |
| 新增 | 基线里没有这个键 | 四种语言过内容判据；不用登记 |
| 改动 | 键还在，中文变了（没有中文的来源看英文）；**换键同时改字也算**：同一字典文件里一行换一行的位置配对（i18n.ts、CC 桌面版、Keycloak、四语表），或同一来源里去掉占位与标点后相似度够高的一删一加 | 变了的语言过内容判据，**必须登记用户同意**（R-CHG） |
| 译文改动 | 中文没变，其他语言变了；含英文 msgid 改写、中文不变 | 变了的语言过内容判据，不用登记 |
| 移动 | 换了键，四种语言一个字没变 | 不判 |
| 删除 | 键没了 | 列进清单，不拦 |

## 判据

`block` 拦，`warn` 只在清单里标黄。词表在 `scripts/i18n/copy-words.tsv`（规则 / 语言 / 级别 / 正则 / 说明），按角色的上限在 `scripts/copygate/rules.py`。词表按两份语料校准：499d4417 的改前 → 改后，和锁定镜像里 Frappe / ERPNext 官方中文、泰文译文（官方写法被误拦的规则要收窄）。

**中文是权威语言**：解释原因（R-WHY）、推测（R-HEDGE）、交代系统行为（R-NARR）三类，英文或泰文命中而同一词条的中文不拦时降为标黄 —— 中文「资料未确认，无法推送」的英文写成 ", so it cannot be pushed" 是同一个事实加后果。泰文 เนื่องจาก / เพราะ 在出错角色只标黄（Frappe / ERPNext 官方泰文报错这样写）。口语、第一人称、寒暄按每种语言各自判。

| 规则 | 拦什么 | 例（坏 → 好） |
| --- | --- | --- |
| R-LEN | 按角色超过拦截线（中文字数、英文词数、泰文按字形数 = 中文 × 2.7，全局 120）；泰文只在中文也超了提醒线时才拦 | 见下表 |
| R-SENT | 按钮 / 菜单 / 标签 / 状态 / 标题 / 占位 / 空态 / 选项最多 1 句；权限引导 3 句；其余 2 句；邮件与替用户说的话不限。英文缩写（incl. / excl. / no. / Co., Ltd.）不算句末 | 「此单据为转账凭条。本公司是否录入此类单据？选择后，同类单据将按此处理」 |
| R-WHY | 原因解释：因为、由于、所以、以便、为了、否则、多为、为避免、防止、取决于；because、due to、to avoid、depending on、句首 Since、, so、; otherwise；เพราะ、เนื่องจาก | 「Frappe 按 Host 认站点，所以站点名与域名是同一个值」→「与部署域名相同。」 |
| R-HEDGE | 推测：可能是 / 可能会、或许、也许；may、might、probably、likely（小写，月份 May 不算）；อาจจะ、น่าจะ | 「可能是网络不稳定，请稍后重试」→「网络连接失败，请重试」 |
| R-TALK | 口语：这些 / 这个 / 这张 / 这里、读不了 / 删不掉、还没 / 已经…了 / 停止了，、抵不抵、吧呢啦、你、「要人选」、只统计了、好的 / 马上 / 稍等、再试一次 / 试试；just（just now 除外）、looks like、get started、click here、you'll need；ครับ、ค่ะ、คุณสามารถ、แป๊บนึง。「找不到」是 Frappe 官方出错写法，只标黄，建议「未找到」 | 「这张单还没选推送去向」→「未选择推送目标」；「还没推送」→「未推送」 |
| R-WE | 第一人称与服务腔：我们、我（「我的」除外）、为您、帮您；we、our、us（Contact / About us 除外）；เรา（按搭配认，ติดต่อเรา / เกี่ยวกับเรา 除外） | 「已为您自动选择最近使用的账套」→「已选择最近使用的账套」 |
| R-HINT | 提示语头：请注意、注意：、提示：、说明：、注：、备注：、句首「建议先 / 建议您」；Note:、Note that、Tip:、Heads-up、FYI。「说明：」只在句首算，前面紧挨汉字的段标题（「使用说明:」「操作说明：」）不算（2026-10-19 收窄） | 「请注意：保存后无法修改」→「保存后无法修改」 |
| R-NARR | 交代系统行为：会自动、系统将、接下来、目前、在后台、完成后自动、自动刷新 / 同步 / 重试；单句标黄，≥ 2 句或 ≥ 2 个分句且还在讲"之后会怎样"时拦；出错角色里「系统会 / 将…读取 / 重试 / 继续」与权限引导只标黄 | 「保存后，系统会自动推送并通知相关人员」 |
| R-DEV | 硬痕迹（任何角色拦）：snake_case、`foo()`、环境变量、`docs/`、`.py`、`**`、`` ` ``、legacy / ported / not implemented / under development、旧产品 / 未实现（「未实现汇兑损益」除外）/ 写死 / 开发中；强术语（接口、后端、缓存、载荷、幂等、租约、适配器、端点、降级、本地副本、API、payload、endpoint…；lease 只在锁的语境）产品界面拦、管理后台标黄；弱术语（字段、令牌、队列、参数、拉取、token、webhook、queue、sync）只在 CC 网页 / CC 桌面版 / 登录页 / LINE 卡片拦；「调用」、ID、JSON 只标黄。中 / 泰文里夹的英文开发词（「无法连接 Redis」「token 已过期」）照英文词表判 | 「The truth is in erp_push_logs」 |
| R-MIX | 中 / 泰文里夹不在白名单的英文词（标黄） | 「请在 Foobar 中打开」 |
| R-PAREN | 括号里带解释词（可能、例如、即可、也可以、会自动、它、用户会 / 拿…、被；e.g.、will、also、小写 may）拦；其他 ≥ 6 字的括号标黄；表单名、数字、扩展名不算。单字「或 / 会 / 即 / 如 / 仅 / 也」不判（枚举、会计、立即都会误中） | 「（用户拿它直接登录）」「（可能为网络、密钥或模型名称问题）」 |
| R-EG | 举例只允许在占位、出错、字段说明、权限引导、LINE 问答里 | 标签「金额，例如 5000」 |
| R-HELLO | 欢迎使用、您好、感谢、抱歉、不好意思、敬请期待；welcome、hello、thank you、sorry；สวัสดี、ขอบคุณ、ขออภัย；感叹号（邮件除外） | 「保存成功！」→「保存成功」 |
| R-EMOJI | 装饰性表情（👋 😊 💗 🎉 🙏）拦；导出表里、或状态 / 标签 / 选项打头的状态符号（⚠ ✓ ❗ 🟡 🔍 ☐ ★ …，配图例）只标黄 | 「完成 🎉」→「完成」 |
| R-EMPTY | 空态（最后一段键名是 empty / *Empty）只写事实：暂无… / 没有… / …为空 / 缺少… / No … / … missing / ไม่มี / ไม่พบ；后面不接「请点击…」「Use “…”」 | 「暂无记录。请点击右上角「新建录入」。」→「暂无记录」 |
| R-NAME | 旧称（复用 `tests/test_cc_desktop_name.py` 的正则与 ALLOWED；岗位名「销售助手 / ผู้ช่วยผู้จัดการ」不算） | 「小助手」→「CC 桌面版」 |
| R-ALIGN | 中文 ≥ 4 字、英文 ≥ 12 词、英文词数 / 中文字数 > 1：英文 msgid 里藏着开发说明；日文超中文 3 倍、中文改了译文没跟标黄 | control:33 / 41 / 45 |
| R-LINE20 | LINE 快捷按钮（`dms.card.*BTN_*` 等）四语都不超过 20 字符（按码位数，偏严） | — |
| R-HARD | 新代码引用的词不在字典里、写死（JSX、toast、Desk 弹窗、`frappe.throw`、CC 桌面版控件、页面）、动态 msgid、DocType 新文字不在术语表 | `<span>Toggle Sidebar</span>` |
| R-CONCAT | 把一句话拆成两条词条再拼起来（`{t(a)}{t(b)}`、`__(a) + __(b)`、`_(a) + " — 说明"`）：改成一条带占位符的词条；确属列表拼接的逐条豁免 | `_("Saved") + " — we will push it"` |
| R-CHG | 改已有文案没登记（或登记的出处记录里找不到新文案） | 见下节 |
| R-EXEMPT | 豁免失效（键没了、文案变了）、通配、原因空泛、术语行没写 @来源或正好是开发术语词；登记行格式不对、出处不是 docs/work 记录 | — |
| R-RULES | 同一差异里删改了词表（放宽规则）、又有文案新增 / 改动：词表改动单独提交、单独过 CI | — |

### 角色

依次看：

1. 只被邮件模板引用的词条 → 邮件（邮件模板以外也用到的，按界面上的角色判）。
2. 键名模式（`copy-words.tsv`）：`COMPANY.*Text`、`COMPANY.*handToCC*`（CC 网页预填进输入框、由用户自己发给 CC 的话）→ 替用户说的话，只判长度、开发痕迹、旧称；`common.scan.*` → 权限引导；`dms.card.*` → LINE 卡片。
3. 调用点（`sources.callsite_roles`）：`frappe.throw(_())` / `msgprint` → 出错，`frappe.confirm` → 确认，`show_alert` → 提示，`subject=` → 邮件，`placeholder=` / `:placeholder` → 占位，`description:` → 字段说明，`aria-*` / `screenReaderInstructions` → 读屏。键名带 instructions 却没交给读屏的，换不来读屏的宽上限。
4. DocType JSON：label → 标签，description → 字段说明，Select 选项 → 选项（英文取值码不判 snake_case，中 / 泰 / 日文照判）。键列写的就是 msgid 的行也看 DocType。
5. 键名：任意一段带 err / error / failed / invalid / missing / unknown / duplicate / ambiguous / not_found 或最后一段 `no_*` → 出错（优先于 code / action / save / header / column / status）；最后一段是 empty / *Empty → 空态；placeholder → 占位；hint / help / note / tip / desc / subtitle / step / instruction → 提示；title / heading / header → 标题；col / label → 标签；status / state / code、`*_empty` 差异码 → 状态；最后一段是 btn / action / save / delete / confirm / submit / cancel → 按钮；最后一段以 nav / menu 开头 → 菜单。按键名猜成状态 / 标签 / 标题 / 菜单、文案却是带句号的整句时按未知判。
6. 英文以问号结尾 → 确认；其余未知（上限同提示，键名不带角色词换不来更宽的上限）。

### 界面面

- 纯终端界面（CC 网页、CC 桌面版、登录页、LINE 卡片、导出给客户的四语表）：强弱开发术语都拦。
- 产品 Desk（租户站点的语义键、msgid 文案、租户能读的 DocType 的标签 / 说明 / 选项）：强术语拦，弱术语标黄。
- 管理后台（控制面 `pearnly_control`；权限只有 System Manager / Administrator 的 DocType、以及 hidden 字段的标签 / 说明 / 选项；只被控制面代码调用的共用层 msgid，代码清单在 `sources.ADMIN_CODE`，登记的共用层模块由测试守着只被控制面引用）：术语只标黄。

中文提醒线 / 拦截线（字；英文词、泰文字符另算；英文词、数字、中文里夹的泰文表单名各记 2 字，占位符记 2 字）：

| 按钮 / 菜单 | 标签 / 选项 | 状态 | 标题 / 占位 | 空态 | 提示 / 未知 | 出错 | 确认 | LINE 卡片 | 字段说明 | 读屏 | 替用户说的话 | 权限引导 | 邮件 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8 / 12 | 10 / 16 | 8 / 12 | 12 / 16 | 10 / 16 | 20 / 40 | 30 / 45 | 40 / 50 | 30 / 45 | 30 / 40 | 40 / 60 | 40 / 60 | 40 / 80 | 80 / 150 |

CC 网页的按钮、菜单、状态硬上限 10 字。

## 改已有文案：先给用户看，再登记

1. `python3 scripts/check-copy.py --report` 生成清单，把第 1 节（改动已有文案，四语旧 → 新，新文案不截断）贴进本任务的 `docs/work/` 记录，给用户看。
2. 用户同意后登记 `scripts/i18n/copy-approved.tsv`：`来源:键 <TAB> 语言 <TAB> 新文案 <TAB> 同意日期 <TAB> 出处`。**出处必须写那份 `docs/work/*.md` 记录的路径**，闸门会到记录里逐字找新文案（原文或清单里的写法都认），找不到这行登记不算数、报 R-EXEMPT。新文案必须和当前值完全一致，之后再改要新登记一行。
3. 也可以执行 `python3 scripts/check-copy.py --approve --source "docs/work/<记录>.md …"` 一次写进当前全部未登记的改动（**只在用户确认之后执行**）；记录里找不到的新文案会被列出、整批不写。登记合入 main 后是历史记录，不删。

机器没法证明用户真的同意过：出处记录只保证改动被看见、可追查，开发代理不能凭一句"主对话确认过"就自己放行。本次新增的登记行会单列在清单第 5 节。

## 例外：逐条豁免

`scripts/i18n/copy-exempt.tsv`：`范围 <TAB> 规则 <TAB> 文案 <TAB> 原因`。

- 范围：`<来源>:<键>`（某一条词条）、`术语:<词>@<来源>`（业务专名，只对 R-DEV / R-MIX 生效；**必须写 @来源**，术语不能正好是开发术语词表里的一个词，要写完整专名，例如「Channel Access Token」）、`代码:<路径>`（某处代码引用，R-HARD / R-CONCAT；文案列写清单里的引用原文，拼接写闸门拼出的那一串，如 `… + _(…) + '\n' + _(…)`，按那个文件现在的引用认是否失效）。
- 规则：逗号分隔的规则 ID，不许 `*`；R-CHG、R-EXEMPT、R-RULES 不能豁免。
- 文案：登记时的完整当前文案（有中文写中文，换行写 `\n`）；文案一变豁免就失效，闸门报「豁免已失效，请重新确认」。
- 原因：具体理由，≥ 6 字，不能只写「历史遗留 / 需要 / 暂时 / 同上」。

先改文案；只有已经定稿的例外才登记（现有：LINE 加好友欢迎语、CC 输入框占位「今天我能为您做些什么？」与首页问候、「拍摄提示：」、后台专名）。改了已登记豁免的键（例如 CC 首页问候、输入框占位），要在同一提交里更新这几行，否则豁免失效变红。改 `copy-words.tsv` 会出现在清单的「规则改动」一节；放宽词表和文案改动放在同一差异里会被 R-RULES 拦；`tests/test_check_copy.py` 收录的已知坏例必须仍被拦 —— 不靠放宽规则过闸。

## 清单格式

`--report` 输出 Markdown：抬头一行计数（新增、改动（已登记 / 未登记）、删除、译文改动、移动、拦截、标黄、豁免），然后 1 改动已有文案（需用户先看）、2 新增文案（另列「提示类新增」请人判断是否该存在）、3 删除文案、4 译文改动与移动、5 登记与豁免变动、6 拦截项（全文与位置）、7 规则改动。四种语言并排，CC 网页没有日文显示「—」，表格里 `|` 转义、换行显示为 ⏎、超过 60 字截断（第 1 节的新文案不截断）。

## 做不到的

- 模型生成的回复不在字典里，这道闸管不到；CC 的口径由系统提示词约束。
- 中文、泰文没有分词，词表按字面匹配；泰文句数不判，R-NARR 按空格分段，以中文为准降级。个别合法用法要逐条登记。
- 代码抽取是正则 + 简易词法：写进普通常量再传给界面（`const msg = "…"`）、Python 接口返回的 `{"message": "…"}` 不判；按调用点定角色也只认常见写法。
- LINE 快捷按钮的 20 字符怎么数泰文组合符没有核实，按码位数（偏严）。
- 用户是否真的同意过，机器证明不了；登记只保证可见、可追查。
