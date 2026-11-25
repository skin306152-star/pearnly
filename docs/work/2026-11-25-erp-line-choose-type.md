# ERP LINE 收单：没选类型就发文件回「请先选择类型」带按钮（11-25，用户批）

基线 main f18fa1ec8，分支 `pearnly/line-choose-type-first`。主会话 11-25 转用户原话「可以」。

## 改什么
- 没选类型就发图片 / PDF、或点了方向已失效的旧按钮：回「请先选择类型」**并带与 `line_flow.menu()` 同一组原生快捷按钮**
  （买 / 卖 / 收款，按这个账号的权限；同一个函数出按钮，`menu(user, token, prompt)`），原来只回一句「请先选择采购或销售」。
- 已选方向、后面的题（入口 / 公司 / 对方 / 已付 / 付款方式）没答完就发文件：不回这句，把当前那一题连按钮重新问一遍（`line_flow.not_ready` →
  `_next`，同点按钮那一步的出题）。
- `intake.receive` / `receive_chat` 两处服务端校验（方向不合法）文字换成新句、不带按钮。`receive_chat`（CC 对话附件）那处能走到：
  CC 工具交上来的方向不是采购 / 销售时；语义不变，只换字。
- 旧键 `Choose purchase or sales first` 再无调用方，已从术语表删除；翻译包由 `scripts/i18n-build.py` 重新生成。
- Cowork 的 LINE 入口没有同类句子：Cowork 的「Purchase or sales?」是识别后带按钮的提问，不是没选类型的拦截句，不动。

## 文案清单（`check-copy.py --report` 第 1 节，用户 11-25 已同意）

| 来源·键 | 角色 | 中文 旧 → 新 | English | ไทย | 日本語 |
|---|---|---|---|---|---|
| pearnly_erp_portal·Choose a type first | 出错 | 请先选择采购或销售 → 请先选择类型 | Choose purchase or sales first → Choose a type first | กรุณาเลือก ซื้อ หรือ ขาย ก่อน → กรุณาเลือกประเภทก่อน | 先に仕入か売上を選んでください → 先に種類を選んでください |

## 测试
- `integrations/test_line_intake.py`：没选类型发文件 → 新句 + 按钮（只有采购权限时只有「买」）；按权限出按钮（买 / 卖 / 收款）；
  方向已选、题没答完发文件 → 重问当前那一题（同一条回复、同一组按钮，不收件）；方向已失效的旧按钮 → 新句 + 按钮。去掉修法四条失败。
- 真机（三星 LINE）由主会话排，代码与离线先交。

## 验证
- 离线：生产镜像里 `test_line_intake` 148 条过（新加 5 条：没选类型 / 按权限出按钮 / 题没答完重问 / 方向权限被收回 / 方向失效的旧按钮）；去掉修法前 4 条失败。
- 全闸门 exit=0；候选离线 LANE EXIT 0；`check-copy.py` 拦截 0（已登记）。
- 影响清单（`check-impact.py`）：自动那条 `erp.line.webhook-sim` 没跑——该冒烟只发文字（เมนู / ซื้อ / ยกเลิก / 乱打），本次改的是发文件与旧按钮两条路，
  已由上面的单测覆盖；手动各条（`erp.line.chat` 等）要三星真机，由主会话排。
- code-review（medium）：发现 1 / 真问题 1 / 修 1 / 约 5 分钟（方向已选但此刻没有该方向权限时会继续重问后面的题 → 改为按权限给类型按钮，补测试）。
