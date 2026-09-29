import unittest

from scripts.attempt_swebench import image_name, prediction_patch
from scripts.select_swebench import select_rows


class SwebenchSelectionTests(unittest.TestCase):
    def test_prediction_excludes_test_edits_but_keeps_source_changes(self):
        raw = ("diff --git a/pkg/core.py b/pkg/core.py\n--- a/pkg/core.py\n+++ b/pkg/core.py\n"
               "@@ -1 +1 @@\n-old\n+new\n"
               "diff --git a/tests/test_core.py b/tests/test_core.py\n"
               "--- a/tests/test_core.py\n+++ b/tests/test_core.py\n@@ -1 +1 @@\n-old\n+new\n")
        patch, excluded = prediction_patch(raw)
        self.assertIn("diff --git a/pkg/core.py", patch)
        self.assertNotIn("a/tests/test_core.py", patch)
        self.assertEqual(excluded, ["tests/test_core.py"])

    def test_instance_image_name_matches_official_image_naming(self):
        self.assertEqual(image_name("pallets__flask-4045"),
                         "swebench/sweb.eval.x86_64.pallets_1776_flask-4045:latest")

    def test_selection_is_repository_stratified_and_input_order_independent(self):
        rows = [
            {"repo": "z/repo", "instance_id": "z-2"},
            {"repo": "a/repo", "instance_id": "a-1"},
            {"repo": "z/repo", "instance_id": "z-1"},
            {"repo": "b/repo", "instance_id": "b-1"},
        ]
        selected = select_rows(rows, 2)
        self.assertEqual([row["repo"] for row in selected], ["a/repo", "b/repo"])
        self.assertEqual(selected, select_rows(list(reversed(rows)), 2))
