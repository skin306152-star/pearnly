# Cowork 复核工作簿导出（第六批）

第六批基线为新平台 `669888001871f5e8cc4bbc783b0ff6c2499d76c5`；主任务已核对 `baseline-sixth-start.json`、`ocr-runtime-sixth-start.json`，旧生产仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。固定源码来自 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。本记录只覆盖导出准备、XLSX 字节适配及纯测试，不代表前端、外部 ERP 推送、生产部署或用户验收。

## 归属与稳定入口

`pearnly_cowork.roundtrip.export_mapping` 是纯校验与投影；只依赖同域 `money`。`export_writer` 用 openpyxl 和内存 BytesIO 生成工作簿，单向依赖 mapping/spec/money。`export` 是此域字节适配的公开 Python 入口。权限、当前公司/客户、真实 History 与原件关联、HTTP 私有下载归 `roundtrip.service/api`，不由纯 writer 查询或猜测。

```python
from pearnly_cowork.roundtrip.export import (
    ExportError, validate_records, build_review_workbook,
)

records = [{
    "history_id": "authorized-current-site-history-name",
    "merged_fields": {
        "direction": "sales", "date": "2026-09-21", "invoice_number": "001/69",
        "subtotal": "100", "vat": "7", "total_amount": "107",
        "items": [{"description": "สินค้า", "subtotal": "100"}],
    },
    "reason": "",  # 可选，仅待判原因
}]
prepared = validate_records(records)  # detached PreparedWorkbook
content = build_review_workbook(records)  # bytes，不创建文件或取当前时间
```

调用者必须先逐条授权、统一 workspace/client，公开入口限制 1..100 条且任一无权记录导致整件失败，不照搬旧接口静默跳过的行为。纯 writer 接受空列表用于四张空表合同测试；公开入口不开放空导出。历史键要求非空、不含冒号、无两端空格且批内唯一，覆盖字段内同名值；上传者不能通过 `merged_fields.history_id` 冒用来源。

可选 `push_facts={history_id: {actions: {...}, state: {...}}}` 只由可信服务注入。`actions` 使用旧 `_parse_erp_actions` 输出：customer bool、items bool 列表、item_codes、docnum、party_code、direction、stock_acc_group、created_party bool。`state` 为最新 status/reason/adapter。导出会先剥离用户字段中的 ERP 代码、新建/复用、推送状态等再注入事实。成功动作与后续失败状态分别显示，不能据最新失败丢掉真实建档证据，也不能把原成功动作伪装成当前成功。当前原生服务不接 ERP 事实，因此 ERP 代码为空、状态为未知、汇总无新建档；不伪造 Express。

## 固定生产来源与协议

| 来源 | 保持的实际行为 | 新落点 |
| --- | --- | --- |
| `routes/ocr_export_routes.py:201,248` | 销项/进项/待判分流、选中历史批量导出 | 授权记录注入，不能从任意客户端记录直接获取真实 pushfacts |
| `services/excel/erp_workbook.py:168` | 恒定四表及顺序：ขาย、ซื้อ、รอจำแนก、สรุป；进项每票一行、待判琥珀底、汇总只读 | `export_writer.write_workbook` |
| `services/excel/excel_template_th.py:234` | 销项每商品一行；G/H/I 为 `=E行*F行`，J 为 `=I行*0.07`；无 qty/price 时字面金额 | `export_writer._sales` |
| `services/excel/erp_roundtrip.py` | 固定泰语表头/列宽、PRN 键、状态文本、必需回导列 | `roundtrip.spec` 单一来源 |
| `services/excel/xlsx_style.py` | 深蓝表头、无自定义字体、新建浅绿/复用灰蓝、边框、金额/数量/日期格式、冻结首行 | writer 内同值样式，逐单元格比较 |
| `services/excel/erp_money.py` 与 reader | Decimal HALF_EVEN、7% VAT、印刷 VAT 容差 0.02、回读时固定派生公式 | `roundtrip.money` 唯一金额合同 |
| `services/erp/export_actions.py:37,76,270` | 已成功动作、最新状态、新建主档汇总分别处理 | 注入事实；不迁数据库/外部写入 |

销项实际 **23 列**，PRN 隐藏在 V22、存货科目组 W23；旧注释“22 列 / 15–21 回导列”已过时。进项 18 列、PRN R18；待判 14 列、PRN N14；汇总 5 列。泰语 sheet/header 是回导线协议，旧 `lang` 参数也没有改写它们；不随界面语言翻译。税号/分支/票号保持字符串及前导零。样式、列顺序和四条固定公式没有改动，文件名和当前时间交调用服务管理。

待判表保留可见金额，但旧 reader 的 pending 结果只保留来源键、票号、日期、空 items 和原因，不携带金额/税号/对手方。它是待人工裁决的记录，不可描述为已能直接重推的财务票。汇总只接受本批真正新建的商品/客户/供应商，复用档不进入清单；同编码按旧规则去重。

## 金额保护与明确差异

导出前按真实 reader 规则预测税基、VAT、合计；已有数值只要与预测不相等就拒绝整件。总額不能当作税前明细兜底，不能把税前金额缺失静默变成零，不能忽略不一致的 amount/total/subtotal 或 qty/quantity、unit_price/price 别名。金额运算使用 Decimal；NaN/Infinity/bool/非法金额及超出 Excel 15 位有效数字范围拒绝，防止 XLSX 数值单元格改变金额。

新平台 `ThaiInvoice.items[].subtotal` 是正常字段。经本批明确授权，仅在没有 amount/total 时把它适配成 amount；若同时提供多个金额必须相等。零 subtotal 用字符串准备值经过旧 truthiness 取值后仍写成数字 0，原始字段不被修改。含金额的原识别 100/7/107 已实际导出并回读守恒。旧默认 items 为空时用 total 当 base 会在回导后加税，本批明确 `roundtrip.export_sales_items_required`，不猜税前数。

进项税基为 0 且税额缺失时，旧 reader 故意返回 None/None/None；因此导出拒绝该形状，真正零金额票须显式 VAT=0。其他旧 truthiness 别名会丢失的 numeric-zero 形状若不能无损往返也会被拦截，不悄悄套不同算法。印刷 VAT 仍由共享 reader 的 0.02 容差决定；若原印刷值会被替换而改变原金额，则拒绝导出。

字面行 VAT 有明确 Decimal 差异：1.5 × 7% 在旧 float `round(..., 2)` 为 0.11，在 HALF_EVEN 为 0.10。测试单独断言此差异而不放宽普通样本对比；真实单据印刷 VAT=0.10，两个版本回读总额均为 1.60。固定 Excel 公式仍保持源文字，未用另一种公式取代。

所有用户文字单元格强制字符串类型；以 `=` 开始的名称、票号或备注不再成为 Excel 公式。只有 writer 中四条固定公式标记为公式。超长 Excel 文本（>32767）或非法 XML 控制字符拒绝，不截断；非法非空销售日期拒绝，不静默导成空值。合法日期沿用源六格式和佛历减 543，纯层不读取时钟。

真实多页 History 首 page.fields 可能已经合并全部明细，后页仍留原始识别证据。旧导出再逐页拼 items 会重复第二页商品。当前服务仅对 binding_complete 且本 History 位于 binding_records 的可信原件使用首 canonical fields；无绑定多页拒绝 `roundtrip.export_multipage_unverified`。本 writer 不按商品内容去重：两件相同商品仍是两行；误拼的 [100,200,200] 与原总额 321 不守恒会被拒绝。服务投影和绑定权限由主任务 Site 测试覆盖。

错误类为 `ExportError(code, record_index=1-based, field=...)`；不能把错误当作成功的空文件。错误码包括 records/record/history/facts/text/date/number_invalid、number_precision、amount_missing、line_amount_missing、sales_items_required、amount_not_roundtrippable。scope_mixed 与 multipage_unverified 由服务层补充。

## 本地验证

专用隔离 bench：Python 3.14.7，镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，固定挂载本工作树。仅运行纯离线测试，没有连接 Site 或请求 ERP/OCR，也没有启动、停止、迁移容器。

- `test_cowork_roundtrip_export.py`：16 项通过，覆盖完整四表/公式/隐藏键、schema subtotal/零金额、别名冲突、无明细、税额容差、真实多页重复形状、重复商品保留、未可信 pushfacts 剥离、成功动作与最新失败、用户文字公式防护、日期/前导零、精度/非法文本、记录和事实作用域、待判非财务语义。
- `test_cowork_roundtrip_export_source_parity.py --source /tmp/cowork-invoice-source`：绑定六文件 SHA-256 后只编译纯 AST，移除旧应用 imports 并注入固定钱/规格模块。60 个合成工作簿、9,999 单元格（值、类型、字体、底色、边框、对齐、数字格式、保护、列宽/隐藏、冻结/表头高度）与 196 个完整回读文档均一致；另验证 1 个有意半分差异。工作簿 ZIP 的创建时间等元数据不属于视觉/业务合同，没有对二进制字节做假等同。

六个固定 hash 在差分测试 `HASHES` 常量可复查，源提交不变才允许执行。权限、受理/重放/字符收费、新历史而非更新原历史、私有下载由主任务报告原生 Site 证据。本批没有前端、Excel 人工打开编辑、泰国会计复核、真实 ERP 重推或生产部署验收。

## 后端四语

唯一来源 `scripts/i18n/messages.tsv` 仅追加本批 **22** 个 Cowork 语义键（6 个解析码、12 个 writer 校验码、4 个服务码），既有 TSV 行保持逐字不变。沿既有 `i18n-build.py` 的 read/existing/write 函数只生成 Cowork en/zh/th/ja，四份各 **288** 条唯一 msgid，没有重写其他 App 或前端。生成器校验通过；对 roundtrip 全域 22 个错误码做差集，缺漏 0、重复 msgid 0、跨 App 同名冲突 0。`ocr.roundtrip_not_matched` 通常用于内部通用表格分流，仍有可读兜底文案。

这不是全仓严格 i18n 通过声明：原先非本批 Intake 骨架等缺漏保持原状，也没有增加 ignore。泰日文案完成含义/术语核对，尚未经母语会计人工验收；原生 Site 翻译加载由主任务单独验证。固定泰语 XLSX 表头和状态值属于旧回导协议，不随四语界面文本变化。
