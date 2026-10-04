import ast
import re
import unittest
from pathlib import Path

from telebt.locale import TEXT


class LocaleTests(unittest.TestCase):
    def test_arabic_and_english_cover_ui_and_validation_messages(self):
        source_root = Path(__file__).resolve().parents[1] / "src" / "telebt"
        ui_tree = ast.parse((source_root / "ui.py").read_text(encoding="utf-8"))
        ui_keys = {node.args[1].value for node in ast.walk(ui_tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_l" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)}
        errors = set()
        for path in source_root.glob("*.py"):
            errors.update(re.findall(r'ValidationError\("([a-z_]+)"\)', path.read_text(encoding="utf-8")))
        expected = ui_keys | errors
        self.assertEqual(TEXT["ar"].keys(), TEXT["en"].keys())
        self.assertFalse(expected - TEXT["ar"].keys())


if __name__ == "__main__":
    unittest.main()
