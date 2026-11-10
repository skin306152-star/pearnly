# CC 标准组件库（components/ui）

CC 对话页（`frontend/assistant`）的通用控件只从 `frontend/assistant/src/components/ui` 取。这套组件来自 shadcn/ui，2026-11-10 由用户拍板整套搬进来。
现有界面一行不动，**改到哪换到哪**：以后谁改到哪个界面，就把那里手写的通用控件换成库里的组件；新写的界面直接用库。

外观仍然只有一套，就是 [ccs](ccs.md)。组件只负责结构、键盘与无障碍行为、API；颜色、尺寸、圆角、深浅色全部走 `globals.css`。
库里的按钮、输入框、开关、表格、页签、菜单、弹窗挂的就是现有的 `cc-*` 类，所以换上之后看起来不会变。

## 1. 有什么

清单与来源见 [components/NOTICE.md](../../frontend/assistant/src/components/NOTICE.md) 的登记表。名字与属性照 shadcn 官方文档，查用法直接看 `ui.shadcn.com/docs/components/<名字>`。

| 要做的 | 用 |
| :- | :- |
| 按钮、图标按钮、按钮组、开关按钮、分段 | `Button`、`ButtonGroup`、`Toggle`、`ToggleGroup` |
| 输入、多行、带前后缀、下拉、原生下拉 | `Input`、`Textarea`、`InputGroup`、`Select`、`NativeSelect` |
| 勾选、单选、开关、标签、字段（标签 + 说明 + 出错） | `Checkbox`、`RadioGroup`、`Switch`、`Label`、`Field` |
| 弹窗、确认、侧拉、气泡、菜单、提示 | `Dialog`、`AlertDialog`、`Sheet`、`Popover`、`DropdownMenu`、`Tooltip` |
| 表格、页签、卡片、列表行、空状态、提示框、徽章、键、头像、进度、转圈、骨架、分隔线、折叠 | `Table`、`Tabs`、`Card`、`Item`、`Empty`、`Alert`、`Badge`、`Kbd`、`Avatar`、`Progress`、`Spinner`、`Skeleton`、`Separator`、`Collapsible` |
| 对话：消息、气泡、附件、工具步骤、提问卡、代码块、对话里的一行标记 | `Message`、`Bubble`、`Attachment`、`Trace`、`Questionnaire`、`CodeBlock`、`Marker` |

没搬的件（日历、图表、命令面板、轮播、可拖分栏、Toast、消息滚动 message-scroller 等），以及不搬的理由，见 [评估](../work/2026-11-10-cc-ui-kit-评估.md) 第 2 节。
需要时按第 4 节补进来，不在业务组件里另写一个。

### 按钮的取值

`Button` 的 `variant` / `size` 用 shadcn 的名字，落到现有按钮类上：

| `size` | 现有类 | 用在 |
| :- | :- | :- |
| `default`、`lg` | `.cc-btn`（32，手机 44） | 设置、弹窗、列表页的文字按钮 |
| `sm` | `.cc-btn-sm` | 列表行里的小按钮 |
| `xs` | `.cc-card-btn`（24，手机 44） | 对话里卡片底部的按钮（ccs 第 2 节） |
| `icon`、`icon-lg` | `.cc-icon-btn` | 只有图标的按钮 |
| `icon-sm` | `.cc-row-btn` | 行尾的小图标钮（「···」「复制」） |
| `icon-xs` | `.cc-card-btn[data-icon]` | 卡片里的圆形「×」 |

`variant`：`default` 是主要按钮（反色实心，`data-primary`），`outline` 是普通按钮，`secondary` 是浅灰实心，`ghost` 无底色，`destructive` 是红字（`data-danger`），`link` 是 `.cc-link-btn`。

## 2. 规则

1. **通用控件只用库里的组件**。业务组件（`components/` 下除 `ui/` 以外的地方、`assistant.tsx`、`lib/`）里不写：
   - K1：原生的 `<button>`、`<input>`、`<select>`、`<textarea>`、`<table>`、`<dialog>`；隐藏的选文件框 `<input type="file">` 不算；
   - K2：`radix-ui`、`@shadcn/react`、`@base-ui` 这些底层件，只在 `components/ui` 里引；
   - K3：自己写 `role="switch|checkbox|radio|tab|dialog|menu|…"` 拼出来的交互控件；
   - K4：直接挂通用外观类（`cc-btn`、`cc-card-btn`、`cc-icon-btn`、`cc-row-btn`、`cc-link-btn`、`cc-input`、`cc-switch`、`cc-table`、`cc-tabs`、`cc-alert`、`cc-chip`、`cc-kbd`、`cc-spinner`、`cc-empty`、`cc-dialog*`）。
   闸门 `python3 scripts/check-frontend-kit.py` 按上面四条计数，结果在提交钩子和 CI 里同一处检查。
2. **存量只许减少**。11-10 合入时，现有手写的部分按文件 × 规则登记在 `scripts/frontend-kit-baseline.json`。哪个文件超出基线就失败，新文件的基线是 0。
   换掉几处以后，跑 `python3 scripts/check-frontend-kit.py --update` 把基线降下来，和改动在同一个提交；`--update` 遇到增加会拒绝。不许手改基线来放行。
3. **改到哪换到哪**。改一个界面的时候，把**这个界面**里手写的通用控件顺手换掉，基线跟着降。只换控件，不改行为，也不改文案。
   不为了换组件专门去动别的窗口正在改的文件；文件归属见总表。
4. **外观只在 globals.css**。组件里只写布局。槽类名 `.cc-ui-*` 的外观写在 `globals.css` 文末「标准组件库」那一块，数值只引用已有的变量（同 ccs 第 1 节）。
   想换样子，就改那一块或者现有的 `cc-*` 类，不在调用处用 `className` 盖颜色、尺寸。
5. **组件里的字走词条**。库里自带的字一律取自 `i18n.ts`，四种语言齐全：能复用已有词条的就复用（`close`、`copy`、`removeFile`、`ask.*`），
   其余放在 `ui.*` 一段。调用方传进来的字照常走词条。
6. **库在最底层**。`components/ui` 不引业务组件、不引对话状态层（`lib/managed-agents`）、不引 `assistant.tsx`（`tests/test_cc_ui_kit.py` 核对）。
7. **对话视口的滚动仍归 assistant-ui**。`Message`、`Bubble` 这些件只管外观；对话区的滚动、跟随、「回到最新」仍由 assistant-ui 的 Viewport 和 `lib/follow-scroll.ts` 负责。
   message-scroller 等到换状态层时再搬，不在同一个视口里叠两套滚动。

## 3. 例子

换一个手写按钮（`components/settings.tsx` 一类的写法）：

```tsx
// 之前
<button type="button" className="cc-btn" disabled={busy} onClick={reconnect}>{st.reconnect}</button>
// 之后
import { Button } from "@/components/ui/button";
<Button variant="outline" disabled={busy} onClick={reconnect}>{st.reconnect}</Button>
```

卡片底部的「跳过 / 提交」：

```tsx
<div className="cc-card-actions">
  <Button size="xs" variant="secondary" onClick={skip}>{ask.skip}</Button>
  <Button size="xs" onClick={submit}>{ask.submit}</Button>
</div>
```

手写的开关（`role="switch"` + `.cc-switch`）换成库里的：

```tsx
import { Switch } from "@/components/ui/switch";
<Switch checked={on} onCheckedChange={setOn} aria-label={st.notifications} />
```

复核表（勾选、可改的格、空值标红）：

```tsx
<Table>
  <TableHeader><TableRow><TableHead /><TableHead>{w.taxId}</TableHead></TableRow></TableHeader>
  <TableBody>
    {rows.map((r) => (
      <TableRow key={r.id}>
        <TableCell><Checkbox checked={r.on} onCheckedChange={(v) => toggle(r.id, v === true)} aria-label={r.name} /></TableCell>
        <TableCell><Input value={r.taxId} aria-invalid={!r.taxId} onChange={(e) => edit(r.id, e.target.value)} /></TableCell>
      </TableRow>
    ))}
  </TableBody>
</Table>
```

换完以后跑：

```bash
python3 scripts/check-frontend-kit.py --update && python3 scripts/check-frontend-tokens.py
```

## 4. 往库里加一个件

1. 从 shadcn-ui/ui 锁定的提交（NOTICE 里写的那个）拷 `apps/v4/registry/bases/radix/ui/<名字>.tsx`，不跑 shadcn 命令；
2. 改写到标准：`cn` 用 `@/lib/utils`，图标换 lucide，`cn-*` 改成 `cc-ui-*`，组件里不合 `check-frontend-tokens.py` 的数值类去掉、外观写进 globals.css；
   能用现有 `cc-*` 类的直接挂现有类；英文字换成词条；
3. 同一提交：NOTICE 登记表加一行，`scripts/impact/shared-parts.toml` 的 `cc-ui-kit` 补路径和标记，展示页 `src/kit/gallery.tsx` 摆出这个件；
4. 新的运行依赖锁精确版本，并在评估里写清楚为什么要加它。

升级上游提交另开一批：逐个文件对比上游的差异，只拿行为修正，外观仍然走 ccs。

## 5. 展示页

`frontend/assistant/kit.html`（源码 `src/kit/`）只在开发环境：

- 本机：`npx --prefix frontend/assistant vite`，然后打开 `/assets/pearnly_core/assistant/kit.html?lang=th|zh|en|ja&theme=light|dark`；手机宽度看窄窗口（< 768 走手机变体）；
- 测试机：实时源码构建时设 `CC_KIT=1`，产物里才有 `kit.html`。从 main 构建的生产镜像里没有展示页（`tests/test_cc_ui_kit.py` 核对 vite 配置）。

展示页里的字也只用现有词条，文案闸门照常扫它。
