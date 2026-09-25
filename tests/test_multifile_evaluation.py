import tempfile
import unittest
from pathlib import Path

from scripts.audit_multifile_zero import check_zero
from scripts.evaluate_history_outputs import HistoricalAgent
from scripts.evaluate_multifile import (
    CORRECT_API,
    CORRECT_PRICING,
    create_fixture,
    setup,
    verify,
)


class MultifileVerifierTests(unittest.TestCase):
    def test_zero_price_order_is_not_an_empty_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_fixture(root)
            (root / "pricing.py").write_text(CORRECT_PRICING)
            (root / "api.py").write_text(CORRECT_API)
            self.assertTrue(check_zero(root)["passed"])
            (root / "api.py").write_text(CORRECT_API.replace("not lines", "subtotal == 0"))
            self.assertFalse(check_zero(root)["passed"])

    def test_oracle_rejects_fixture_and_accepts_correct_public_behavior(self):
        for rate in (17, 9):
            with self.subTest(rate=rate), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                create_fixture(root)
                self.assertFalse(verify(root, rate)["passed"])
                (root / "pricing.py").write_text(CORRECT_PRICING)
                (root / "api.py").write_text(CORRECT_API)
                path = root / "config.py"
                path.write_text(path.read_text().replace("TAX_PERCENT = 5", f"TAX_PERCENT = {rate}"))
                self.assertTrue(verify(root, rate)["passed"])
                for old, new in [
                    (" * line['quantity']", ""),
                    (" + 50", ""),
                ]:
                    (root / "pricing.py").write_text(CORRECT_PRICING.replace(old, new))
                    self.assertFalse(verify(root, rate)["passed"])
                (root / "pricing.py").write_text(CORRECT_PRICING)
                for old, new in [
                    ("config.TAX_PERCENT", str(rate)),
                    ("subtotal >=", "subtotal >"),
                    ("config.SHIPPING_CENTS", "300"),
                    ("not lines or ", ""),
                    ("config.BRAND", "'Wrong Store'"),
                ]:
                    (root / "api.py").write_text(CORRECT_API.replace(old, new))
                    self.assertFalse(verify(root, rate)["passed"], (old, new))

    def test_interrupted_fixture_restores_unknown_effect_and_preserves_human_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = setup(root, HistoricalAgent, "interrupted")
            self.assertEqual(agent.recovered_calls, ["bootstrap"])
            self.assertEqual((root / "bootstrap-count.txt").read_text(), "1")
            self.assertIn("Human Edited Store", (root / "config.py").read_text())
            (root / "pricing.py").write_text(CORRECT_PRICING)
            (root / "api.py").write_text(CORRECT_API)
            self.assertTrue(verify(root, 17, "Human Edited Store", executions=1)["passed"])
            (root / "bootstrap-count.txt").write_text("2")
            self.assertFalse(verify(root, 17, "Human Edited Store", executions=1)["passed"])
            (root / "bootstrap-count.txt").write_text("1")
            (root / "smoke.py").write_text("print('pass')")
            self.assertFalse(verify(root, 17, "Human Edited Store", executions=1)["passed"])
