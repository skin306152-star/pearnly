"""Offline category business predicates; no external models or framework."""
import unittest
from pearnly_cowork.contracts import classification as rules


def preset_tree():
    return [{"id": f"p{i}", "name": name, "children": [
        {"id": f"p{i}c{j}", "name": child} for j, child in enumerate(children)]}
        for i, (name, children) in enumerate(rules._PRESET)]


class Classification(unittest.TestCase):
    def setUp(self):
        self.tree = preset_tree()

    def test_item_priority_over_fuel_vendor(self):
        self.assertEqual(rules.resolve_tag({"seller_name": "PTT", "items": [{"name": "coffee"}]}, self.tree), "ค่าอาหาร/เครื่องดื่ม")

    def test_fuel_and_grocery_vendor(self):
        self.assertEqual(rules.resolve_tag({"seller_name": "PTT"}, self.tree), "ค่าน้ำมันเชื้อเพลิง")
        self.assertEqual(rules.resolve_tag({"seller_name": "Makro"}, self.tree), "สินค้าสำเร็จรูป")

    def test_model_word_fallback_and_unknown(self):
        self.assertEqual(rules.resolve_tag({"category": "银行手续费"}, self.tree), "ค่าธรรมเนียมธนาคาร")
        self.assertIsNone(rules.resolve_tag({"category": "no match anywhere"}, self.tree))

    def test_cross_language_memory_and_custom_name(self):
        self.assertEqual(rules.sanitize_learned("油费", self.tree), "ค่าน้ำมันเชื้อเพลิง")
        self.assertEqual(rules.sanitize_learned("ค่าไฟฟ้า", self.tree), "ค่าไฟฟ้า")
        self.assertEqual(rules.sanitize_learned("Custom", [{"id": "p", "name": "Custom"}]), "Custom")

    def test_missing_tree_preserves_memory_but_unknown_in_tree_does_not(self):
        self.assertEqual(rules.sanitize_learned("Custom", None), "Custom")
        self.assertIsNone(rules.sanitize_learned("Custom", self.tree))

    def test_input_edges(self):
        for value in (None, [], "word", {}):
            self.assertIsNone(rules.resolve_tag(value, self.tree))
        self.assertEqual(rules.item_descriptions({"items": [None, {"description": "fuel"}, {"name": "taxi"}]}), "fuel taxi")
        self.assertEqual(rules.match_user_category("x", self.tree), (None, None))

    def test_presets_are_two_levels_and_names_unique_within_parent(self):
        self.assertEqual(len(self.tree), 16)
        for parent in self.tree:
            self.assertTrue(parent["children"])
            self.assertEqual(len({c["name"] for c in parent["children"]}), len(parent["children"]))


if __name__ == "__main__":
    unittest.main()
