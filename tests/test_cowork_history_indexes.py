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

    def test_existing_sites_get_the_indexes_on_migrate(self):
        # on_doctype_update 只在 DocType 定义变了或新装站点时才被调；已有站点靠迁移补丁补建（2026-10-02 上线时没建出来）
        from pathlib import Path
        from pearnly_cowork.patches import v0_0_11_history_workspace_indexes as migration

        listed = Path(history.__file__).resolve().parents[3].joinpath("patches.txt").read_text()
        self.assertIn("pearnly_cowork.patches.v0_0_11_history_workspace_indexes", listed.split("[post_model_sync]")[1])
        db = MagicMock()
        with patch.object(history.frappe, "db", db):
            migration.execute()
        self.assertEqual({call.args[2] for call in db.add_index.call_args_list}, set(history.INDEXES))


if __name__ == "__main__":
    unittest.main()
