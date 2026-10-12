"""Cowork 报税草稿（2026-10-13）：ภ.พ.30 与进项可抵扣核查、ภ.ง.ด.3 / ภ.ง.ด.53。

确定性计算、零费用：ภ.พ.30 读 Express 回读的 ISVAT（`readback`），预扣税读识别记录与 50 ทวิ 参考记录（`history`）。
只出草稿与核查，不写 Express、不代为申报。纯计算在 `periods` / `pp30` / `input_vat` / `wht`（不连库、不读文件），
`service` 编排、`access` 判权限、`memory` 记所得类型、`workbook` 出工作簿。"""
