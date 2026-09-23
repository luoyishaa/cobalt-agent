import io
import json
import unittest
from unittest.mock import patch

from cobalt.model import DeepSeek, ModelOutputError


class ModelAdapterTests(unittest.TestCase):
    def test_native_tool_call_is_parsed_into_a_typed_turn(self):
        response = {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call-123", "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"src/main.py"}'},
                    }],
                },
            }],
            "usage": {"prompt_tokens": 31, "completion_tokens": 9},
        }
        captured = {}

        def fake_open(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return io.BytesIO(json.dumps(response).encode("utf-8"))

        with patch("urllib.request.urlopen", fake_open):
            turn = DeepSeek(api_key="test-only", model="test-model").complete(
                [{"role": "user", "content": "read the file"}], []
            )
        self.assertEqual(turn.calls[0].name, "read_file")
        self.assertEqual(turn.calls[0].arguments, {"path": "src/main.py"})
        self.assertEqual(turn.prompt_tokens, 31)
        self.assertEqual(captured["request"].get_header("Authorization"), "Bearer test-only")
        body = json.loads(captured["request"].data)
        self.assertEqual(body["model"], "test-model")

    def test_malformed_tool_arguments_fail_closed(self):
        response = {
            "choices": [{"finish_reason": "tool_calls", "message": {
                "content": None,
                "tool_calls": [{"id": "call-1", "function": {"name": "create_file", "arguments": "[]"}}],
            }}],
        }
        with (
            patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode("utf-8"))),
            self.assertRaisesRegex(ModelOutputError, "invalid structured response"),
        ):
            DeepSeek(api_key="test-only").complete([{"role": "user", "content": "x"}], [])
