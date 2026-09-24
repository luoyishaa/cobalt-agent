import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_multiturn import verify


class MultiturnGraderTests(unittest.TestCase):
    def test_wrong_tax_and_constant_answer_fail_independent_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "invoice.py"
            source.write_text("def total(amounts):\n    return (sum(amounts)*117+50)//100\n")
            self.assertTrue(verify(root, 17)["passed"])
            self.assertFalse(verify(root, 9)["passed"])
            source.write_text("def total(amounts):\n    return 11700\n")
            self.assertFalse(verify(root, 17)["passed"])

    def test_correct_value_with_mutated_input_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "invoice.py").write_text(
                "def total(amounts):\n    n = sum(amounts)\n    amounts.clear()\n    return (n*109+50)//100\n")
            self.assertFalse(verify(root, 9)["passed"])
