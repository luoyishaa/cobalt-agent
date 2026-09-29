import json
import tempfile
import unittest
from pathlib import Path

from scripts.summarize_swebench import summarize


class SwebenchSummaryTests(unittest.TestCase):
    def test_release_gate_needs_three_official_resolutions_across_two_repositories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = [{"instance_id": f"owner{i}__repo{i}-{i}", "repo": f"owner{i}/repo{i}"}
                     for i in range(10)]
            ids = [task["instance_id"] for task in tasks]
            attempts = []
            for instance_id in ids:
                attempts.append({"instance_id": instance_id, "status": "started"})
                attempts.append({"instance_id": instance_id, "status": "finished",
                                 "report_file": f"{instance_id}.json"})
                (root / f"{instance_id}.json").write_text(json.dumps({
                    "execution_mode": "container", "container_image_id": "sha256:example",
                }), encoding="utf-8")
            predictions = [{"instance_id": instance_id} for instance_id in ids]
            official = {"submitted_ids": ids, "resolved_ids": ids[:3], "infra_failure_ids": []}
            result = summarize({"tasks": tasks}, attempts, predictions, official, root)
            self.assertTrue(result["release_gate_met"])
            self.assertEqual(result["official_resolved"], 3)
            (root / f"{ids[0]}.json").write_text(json.dumps({"execution_mode": "local"}),
                                                  encoding="utf-8")
            self.assertFalse(summarize({"tasks": tasks}, attempts, predictions, official,
                                       root)["release_gate_met"])
