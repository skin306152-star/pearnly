# 界面文案

界面文案只写完成操作必需的字：官方、简短、陈述事实。不写原因、过程、提示、欢迎语和开发说明，不用口语；Windows 端在用户可见处只叫「CC 桌面版」。想加说明时，先考虑能否用状态、图标、禁用态或官方组件自带文案表达。

`scripts/check-i18n.py` 管"中文 / 泰文不许写死、必须进字典"；`scripts/check-copy.py` 管"进了字典的字是不是碎碎念"。两道闸都在 Development checks 里。

## 怎么用

```bash
python3 scripts/check-copy.py                 # 差异模式：相对 merge-base(origin/main, HEAD)，当前 = 工作树（含未提交）
python3 scripts/check-copy.py --report        # 同时输出 Markdown 清单（交付时贴给用户和测试窗口）
python3 scripts/check-copy.py --report --out /tmp/copy.md
python3 scripts/check-copy.py --repo <另一个工作树> --base origin/main   # 只读地看别的分支
python3 scripts/check-copy.py --audit         # 全量审计存量（只报告，退出码 0；--all 同义）
python3 scripts/check-copy.py --unused-keys   # 术语表里代码没引用的键（参考）
python3 tests/test_check_copy.py              # 闸门自己的测试（改词表、改规则时必跑）
```

退出码：差异模式有拦截 = 1，取不到基线 = 2。基线：环境变量 `COPY_GATE_BASE`（CI：PR 的 base.sha、推 main 的 event.before）与 HEAD 的 merge-base；没有就用 `origin/main`；都取不到判失败，不跳过。本地先 `git fetch origin main`，`origin/main` 旧了只会让检查范围更宽，不会漏。

接入点：CI（`.github/workflows/development.yml`，结果写进 Step Summary）、共享提交钩子（暂存快照；分支还没变基到含闸门的 main 时提示后跳过，变基后由分支 CI 与推 main 前的 CI 检查兜住）、`~/pearnly-devtools/gate.sh`（按 workflow 的 run 行执行，不用改）。

## 扫哪些来源

| 来源 | 词条 id（`来源:键`） | 判什么 |
| --- | --- | --- |
| 术语表 `apps/<app>/<app>/i18n/messages.tsv` | `<app>:<key>`，无 key 用 en（msgid） | 内容判据，四种语言 |
| CC 网页 `frontend/assistant/src/i18n.ts` | `assistant:<对象>.<键路径>`（按键比对，一行拆多行不算改动） | 内容判据 |
| CC 桌面版 `companion/src/companion/i18n.py` 的 `_M` | `companion:<key>`（中文为准） | 内容判据 |
| Keycloak 主题 `messages_*.properties` | `keycloak:<主题>.<key>` | 内容判据 |
| keep-cjk 整文件豁免的四语表（`sources.TABLE_MODULES`） | `table:<模块>#<路径>` | 内容判据 |
| Python（改动文件）：`_()`、`frappe.throw/msgprint` 裸串、assistant 工具声明的 label / ask / ask_when、`*STEP_NAMES` | — | R-HARD：词在不在字典里、有没有写死 |
| Desk JS（改动文件）：`__("…")`、`t("键")` | — | R-HARD |
| CC 网页 TSX / TS（改动文件）：JSX 文本、title / aria-label / placeholder / alt / label、`tr()`、`t()` | — | R-HARD |
| `www/*.html`、`templates/emails/*.html`（改动文件） | — | R-HARD（写死中文 / 泰文）；邮件模板用到的词条按「邮件」角色判 |
| 翻译包 `translations/*.csv` 新增行（术语表里没有的 msgid） | — | 标黄（手工行或改写残留）；新代码不能拿它兜底 |

不扫：给模型的提示词与工具说明（`assistant/definition.py`、OCR 与对账的 prompts / fewshot / schema）、写进 Express / MR.ERP / DMS 的业务数据、日志与断言、`tests/`、`website/`。清单在 `scripts/copygate/sources.py` 的 `NOT_SCANNED`，每项写原因。keep-cjk 再加"规格："整文件豁免时，要在 `TABLE_MODULES`（给用户看的四语表）或 `NOT_COPY_SPEC`（不是界面文案）登记，`tests/test_check_copy.py` 守着。

## 分类

| 类别 | 条件 | 处理 |
| --- | --- | --- |
| 新增 | 基线里没有这个键 | 四种语言过内容判据；不用登记 |
| 改动 | 键还在，中文变了（没有中文的来源看英文） | 变了的语言过内容判据，**必须登记用户同意**（R-CHG） |
| 译文改动 | 中文没变，其他语言变了；含英文 msgid 改写、中文不变（同一 diff 段一减一加或中文相同且英文相似度 ≥ 0.6） | 变了的语言过内容判据，不用登记 |
| 移动 | 换了键，四种语言一个字没变 | 不判；换键同时改字按新增 + 删除算 |
| 删除 | 键没了 | 列进清单，不拦 |

## 判据

`block` 拦，`warn` 只在清单里标黄。词表在 `scripts/i18n/copy-words.tsv`（规则 / 语言 / 级别 / 正则 / 说明），按角色的上限在 `scripts/copygate/rules.py`。

| 规则 | 拦什么 | 例（坏 → 好） |
| --- | --- | --- |
| R-LEN | 按角色超过拦截线（中文字数、英文词数、泰文 = 中文 × 2.7，全局 120） | 见下表 |
| R-SENT | 按钮 / 菜单 / 标签 / 状态 / 标题 / 占位 / 空态最多 1 句；其余最多 2 句；任何角色 3 句以上（邮件除外） | 「此单据为转账凭条。本公司是否录入此类单据？选择后，同类单据将按此处理」 |
| R-WHY | 原因解释：因为、由于、所以、以便、为了、否则、多为、如果…就；because、so that、, so；เพราะ、เพื่อให้ | 「Frappe 按 Host 认站点，所以站点名与域名是同一个值」→「与部署域名相同。」 |
| R-TALK | 口语：这个 / 这张 / 这里、读不了 / 找不到 / 删不掉、还没 / 已经…了、抵不抵、吧呢啦、你、要人选、只统计了；just、actually；ครับ、ค่ะ | 「这张单还没选推送去向」→「未选择推送目标」；「还没推送」→「未推送」 |
| R-WE | 第一人称：我们、我（「我的」除外）；we、our、us（Contact us 除外） | 「我们会帮您重试」 |
| R-HINT | 提示语头：请注意、注意：、提示：、说明：、温馨；Note:、Tip: | 「请注意：保存后无法修改」 |
| R-NARR | 交代系统行为：会自动、系统将、接下来、目前；单句标黄，≥ 2 句拦 | 「DMS 连接中断，本次读取已暂停。系统将从断点继续读取……」 |
| R-DEV | 硬痕迹（任何角色拦）：snake_case、`foo()`、环境变量、`docs/`、`.py`、`**`、`` ` ``、legacy / ported / not implemented、旧产品 / 未实现 / 写死；强术语（接口、后端、缓存、载荷、幂等、适配器、端点、API、payload、JSON…）产品界面拦、管理后台标黄；弱术语（字段、令牌、队列、调用、参数、token、webhook、queue）只在 CC 网页 / CC 桌面版 / 登录页 / LINE 卡片拦 | 「The truth is in erp_push_logs」 |
| R-MIX | 中 / 泰文里夹不在白名单的英文词（标黄） | 「请在 Foobar 中打开」 |
| R-PAREN | 括号里带解释词（可能、如、即、或、仅、也、会、例如）拦；其他 ≥ 6 字的括号标黄；表单名、数字、扩展名不算 | 「（可能为网络、密钥或模型名称问题）」 |
| R-EG | 举例只允许在占位、出错、字段说明、LINE 问答里 | 标签「金额，例如 5000」 |
| R-HELLO | 欢迎使用、您好、感谢；welcome、hello、thank you；สวัสดี、ขอบคุณ；表情；感叹号（邮件除外） | 「保存成功！」→「保存成功」 |
| R-EMPTY | 空态（键以 empty 结尾）只写事实：暂无… / 没有… / …为空 / No …；后面不接「请点击…」 | 「暂无记录。请点击右上角「新建录入」。」→「暂无记录」 |
| R-NAME | 旧称（复用 `tests/test_cc_desktop_name.py` 的正则） | 「小助手」→「CC 桌面版」 |
| R-ALIGN | 中文 ≥ 4 字、英文 ≥ 12 词、英文词数 / 中文字数 > 1：英文 msgid 里藏着开发说明；日文超中文 3 倍、中文改了译文没跟标黄 | control:33 / 41 / 45 |
| R-LINE20 | LINE 快捷按钮（`dms.card.*BTN_*` 等）四语都不超过 20 字符 | — |
| R-HARD | `_()` / `__()` / `tr()` 的词不在字典里（`_()` 两词以下的可能是 Frappe 自带词，只标黄）、JSX 文本与属性写死、`frappe.throw` 裸串、页面写死中文 / 泰文 | `<span>Toggle Sidebar</span>` |
| R-CHG | 改已有文案没登记 | 见下节 |
| R-EXEMPT | 豁免失效（键没了、文案变了）、通配、原因空泛；登记行格式不对 | — |

角色由键名推断（`dms.card.` → LINE 卡片；confirm → 确认；err / error / failed / invalid → 出错；以 empty 结尾 → 空态；placeholder / ph → 占位；hint / help / note / tip / desc / subtitle → 提示；title / heading / section → 标题；col / label / lbl → 标签；status / state / code → 状态；btn / action / save / delete 结尾 → 按钮；nav / menu → 菜单；instructions / aria → 读屏），无键的术语表行看 DocType JSON（label → 标签、description → 字段说明），邮件模板用到的 → 邮件，英文以问号结尾 → 确认，其余未知。

中文提醒线 / 拦截线（字；英文词、泰文字符另算）：

| 按钮 / 菜单 | 标签 | 状态 | 标题 / 占位 | 空态 | 提示 | 出错 | 确认 | LINE 卡片 | 字段说明 | 读屏 | 邮件 | 未知 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8 / 12 | 10 / 16 | 8 / 12 | 12 / 16 | 10 / 16 | 20 / 40 | 30 / 45 | 40 / 50 | 30 / 45 | 30 / 40 | 40 / 60 | 80 / 150 | 30 / 50 |

CC 网页的按钮、菜单、状态硬上限 10 字。占位符 `{0}` / `{name}` / `${…}` 记 2 字，英文词记 2 字。

## 改已有文案：先给用户看，再登记

1. `python3 scripts/check-copy.py --report` 生成清单，把第 1 节（改动已有文案，四语旧 → 新）给用户看。
2. 用户同意后登记 `scripts/i18n/copy-approved.tsv`：`来源:键 <TAB> 语言 <TAB> 新文案 <TAB> 同意日期 <TAB> 出处`。新文案必须和当前值完全一致，之后再改要新登记一行。也可以执行 `python3 scripts/check-copy.py --approve --source "<出处>"` 一次写进当前全部未登记的改动（**只在用户确认之后执行**），写入的行会原样打印。
3. 出处写可追查的位置：主对话日期 + 窗口、`docs/work/` 记录路径或提交说明。登记合入 main 后是历史记录，不删。

机器没法证明用户真的同意过：登记只保证改动被看见、可追查。本次新增的登记行会单列在清单第 5 节。

## 例外：逐条豁免

`scripts/i18n/copy-exempt.tsv`：`范围 <TAB> 规则 <TAB> 文案 <TAB> 原因`。

- 范围：`<来源>:<键>`（某一条词条）、`术语:<词>[@<来源>]`（业务专名，只对 R-DEV / R-MIX 生效）、`代码:<路径>`（某处代码引用，R-HARD）。
- 规则：逗号分隔的规则 ID，不许 `*`；R-CHG、R-EXEMPT 不能豁免。
- 文案：登记时的完整当前文案（有中文写中文，换行写 `\n`）；文案一变豁免就失效，闸门报「豁免已失效，请重新确认」。
- 原因：具体理由，≥ 6 字，不能只写「历史遗留 / 需要 / 暂时 / 同上」。

先改文案；只有已经定稿的例外才登记（现有：LINE 加好友欢迎语、CC 输入框占位「今天我能为您做些什么？」与首页问候、「拍摄提示：」、后台专名）。改 `copy-words.tsv` 会出现在清单的「规则改动」一节，且 `tests/test_check_copy.py` 收录的已知坏例必须仍被拦 —— 不靠放宽规则过闸。

## 清单格式

`--report` 输出 Markdown：抬头一行计数（新增、改动（已登记 / 未登记）、删除、译文改动、移动、拦截、标黄、豁免），然后 1 改动已有文案（需用户先看）、2 新增文案（另列「提示类新增」请人判断是否该存在）、3 删除文案、4 译文改动与移动、5 登记与豁免变动、6 拦截项（全文与位置）、7 规则改动。四种语言并排，CC 网页没有日文显示「—」，表格里 `|` 转义、换行显示为 ⏎、超过 60 字截断。

## 做不到的

- 模型生成的回复不在字典里，这道闸管不到；CC 的口径由系统提示词约束。
- 中文、泰文没有分词，词表按字面匹配：泰文第一人称、句数只作参考；个别合法用法要逐条登记。
- 用户是否真的同意过，机器证明不了；登记只保证可见、可追查。
