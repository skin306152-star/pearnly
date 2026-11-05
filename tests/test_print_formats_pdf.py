"""打印格式要声明 Frappe 原生字段 `pdf_generator`（11-05，CC 1.12.14 实测：ERP 打印页「获取PDF」报 417）。

Frappe 原生打印页（`frappe/www/printview.py`）把打印格式的 `pdf_generator` 原样放进「获取PDF」的地址；这个字段没有默认值，
没设就渲染成字符串 "None"，而 `frappe.utils.print_format.download_pdf` 只收 wkhtmltopdf / chrome，类型校验不过回 417。
所以本仓每个打印格式（各 App 的 print_format/*/*.json）都要写明生成器；我们服务端出 PDF 用的就是 wkhtmltopdf。纯离线。
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"wkhtmltopdf", "chrome"}   # download_pdf 的 Literal["wkhtmltopdf", "chrome"]


def print_formats():
    for path in sorted(ROOT.glob("apps/*/*/*/print_format/*/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("doctype") == "Print Format":
            yield path.relative_to(ROOT), data


class PrintFormatPdf(unittest.TestCase):
    def test_every_print_format_names_a_pdf_generator_download_pdf_accepts(self):
        found = list(print_formats())
        self.assertGreaterEqual(len(found), 7)   # ERP 的七个：领料单、收货单、采购收货、销售、小票、班次、盘点
        for path, data in found:
            with self.subTest(path=str(path)):
                self.assertIn(data.get("pdf_generator"), ALLOWED,
                              f"{path} 没写 pdf_generator：原生打印页「获取PDF」会带 pdf_generator=None，下载回 417")


if __name__ == "__main__":
    unittest.main()
