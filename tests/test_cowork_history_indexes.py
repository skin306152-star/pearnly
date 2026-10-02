"""发票历史表的组合索引随 migrate 建（2026-10-02 m4 压测：没有 workspace 索引，列表与重复检查整表扫）。离线跑。"""
import unittest
from unittest.mock import MagicMock, patch

from pearnly_cowork.pearnly_cowork.doctype.cowork_ocr_history import cowork_ocr_history as history


class HistoryIndexes(unittest.TestCase):
    def test_migrate_adds_each_workspace_first_index(self):
        db = MagicMock()
        with patch.object(history.frappe, "db", db):
            history.on_doctype_update()
        added = {call.args[2]: call.args[1] for call in db.add_index.call_args_list}
        self.assertEqual(added, history.INDEXES)
        self.assertTrue(all(call.args[0] == "Cowork OCR History" for call in db.add_index.call_args_list))
        self.assertTrue(all(fields[0] == "workspace" for fields in history.INDEXES.values()))   # 先按工作区定位


if __name__ == "__main__":
    unittest.main()
