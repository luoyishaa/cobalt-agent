import unittest
from pathlib import Path

from cobalt.domain import ModelTurn
from cobalt.evaluation import run_case


class AnswerOnlyModel:
    def complete(self, messages, tools):
        return ModelTurn("I fixed everything.")


class EvaluationTests(unittest.TestCase):
    def test_external_verifier_rejects_a_confident_but_unchanged_answer(self):
        fixtures = Path(__file__).resolve().parents[1] / "benchmarks"
        row = run_case({
            "id": "false_claim",
            "fixture": "fixtures/grades",
            "request": "Fix the empty average bug",
            "kind": "repair",
            "verifier": ["python", "-m", "unittest", "discover", "-s", "tests", "-q"],
        }, fixtures, AnswerOnlyModel)
        self.assertFalse(row["passed"])
        self.assertEqual(row["failure_category"], "verifier_failed")
        self.assertEqual(row["run_status"], "unverified")

    def test_question_requires_real_read_even_if_words_match(self):
        fixtures = Path(__file__).resolve().parents[1] / "benchmarks"
        row = run_case({
            "id": "unsupported_claim",
            "fixture": "fixtures/entrypoint",
            "request": "Which file defines --port?",
            "kind": "question",
            "answer_terms": ["I", "fixed"],
        }, fixtures, AnswerOnlyModel)
        self.assertFalse(row["passed"])
        self.assertEqual(row["failure_category"], "answer_or_evidence_mismatch")
