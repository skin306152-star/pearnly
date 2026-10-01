# 对账读 PDF 文字层按字节缓存（2026-10-02）

压测窗口第二轮 py-spy：long-worker 22.7% 的 CPU 在 `tabular/readers.extract_pdf_text_pages`（pdfminer 版面分析
`group_textboxes` 占 7%）。等识别的对账每次被唤醒都从头重跑，同一份 PDF 每次读取里还要抽两三次（`bank_intake`
判银行、免费解析、读不出时的提示），每次整份 pdfminer。分支 `codex/pdf-text-cache`，基线 main `0fd1621f`。

## 改法

- 新 `reconciliation/pdf_text.py`：按字节 sha256 缓存，进程内留最近 8 份；队列间用站点缓存（Redis，`pearnly-pdf-text:v1:`，6 小时）。
  缓存不可用（离线、没有站点、Redis 出错）照旧现抽。`bank_intake` 三处改走它。
- `tabular` 按架构是不依赖框架的叶子，`reconciliation/table_io` 是兼容出口（不加业务），所以缓存单独成模块。
- 抽字只取决于字节，结果与直接抽完全相同。

## 核对

- `tests/test_cowork_pdf_text_cache.py` 5 项（同字节只抽一次且各拿副本、另一进程读站点缓存、缓存坏了照抽、进程内只留 8 份、bank_intake 三处都走缓存），加进候选离线道。
- 待做：用重放框架（`scripts/replay-bank-recon.py`）对全部银行对账跑一遍，确认结果逐行不变；上线后压测窗口 py-spy 看 long-worker 抽字占比。
