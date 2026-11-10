# Third-party components

`components/assistant-ui/`, `components/ui/`, `hooks/`, and `lib/utils.ts`
started as the assistant-ui / shadcn copy-in components. They have since been
rewritten to CC's styling standard (`docs/development/ccs.md`) and are kept by
hand: do not regenerate them with the assistant-ui or shadcn command. A
component copied in from there is first rewritten to the standard (`cc-*`
classes and `globals.css` variables) before it goes into `src/`.

They are distributed under the MIT license:

```
MIT License

Copyright (c) 2025 AgentbaseAI Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The quickstart's own code (`app/`, `lib/`, `setup/`, `components/tool-uis/`)
follows the license at the repository root.

## 标准组件库登记表（2026-11-10 起，docs/development/cc-ui-kit.md）

`components/ui/` 是 CC 唯一的标准组件库。shadcn/ui 来源一律锁定提交
`8ab27715521cbd4f87ae6886cd989cbb678f6de7`（2026-11-08），只用 `bases/radix` 一版（底层 `radix-ui` 1.6.5），
按文件手工拷入再改写到 ccs，不跑 shadcn 命令。新增或删掉一个文件，同一提交改这张表（`tests/test_cc_ccs.py`、`tests/test_cc_ui_kit.py` 核对）。

| 文件 | 来源 | 改动 |
| :- | :- | :- |
| `alert-dialog.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/alert-dialog.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `alert.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/alert.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `attachment.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/attachment.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `avatar.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/avatar.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `badge.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/badge.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `bubble.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/bubble.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `button-group.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/button-group.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `button.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/button.tsx` | 按上游 API 手写，外观落到现有按钮类 |
| `card.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/card.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `checkbox.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/checkbox.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `code-block.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/code-block.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `collapsible.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/collapsible.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `dialog.tsx` | shadcn/ui 早期拷入（提交不详） | 已改写到 ccs；11-10 只补导出 |
| `drag.tsx` | 自写（共用拖动） | — |
| `dropdown-menu.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/dropdown-menu.tsx` | 按上游导出名重写，弹出层用 menu.tsx |
| `empty.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/empty.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `field.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/field.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `input-group.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/input-group.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `input.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/input.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `item.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/item.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `kbd.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/kbd.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `label.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/label.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `marker.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/marker.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `marquee.tsx` | 自写（单行截断渐隐） | — |
| `menu.tsx` | 自写，包 radix DropdownMenu（翻边、点外面不穿透、窄屏子菜单换页） | — |
| `message.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/message.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `native-select.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/native-select.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `popover.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/popover.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `progress.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/progress.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `questionnaire.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/questionnaire.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `radio-group.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/radio-group.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `select.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/select.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `separator.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/separator.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `sheet.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/sheet.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `sidebar.tsx` | shadcn/ui 早期拷入（提交不详） | 已改写到 ccs |
| `skeleton.tsx` | shadcn/ui 早期拷入（提交不详） | 已改写到 ccs |
| `spinner.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/spinner.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `switch.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/switch.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `table.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/table.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `tabs.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/tabs.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `textarea.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/textarea.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `toggle-group.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/toggle-group.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `toggle.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/toggle.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |
| `tooltip.tsx` | shadcn/ui 早期拷入（提交不详） | 已改写到 ccs |
| `trace.tsx` | shadcn/ui `apps/v4/registry/bases/radix/ui/trace.tsx` | 改写到 ccs（槽类名 cn-* → cc-ui-*，外观进 globals.css） |

shadcn/ui 与 `@shadcn/react`（提问卡的逻辑）同为 MIT：

```
MIT License

Copyright (c) 2023 shadcn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
