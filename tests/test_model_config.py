import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cobalt.model import (
    AnthropicMessages,
    ChatCompletions,
    ModelOutputError,
    from_config,
)
from cobalt.model_config import PROVIDERS, read_env_file, resolve_config


class ModelConfigTests(unittest.TestCase):
    def test_all_presets_select_with_only_provider_and_its_key(self):
        for name, preset in PROVIDERS.items():
            with self.subTest(provider=name):
                config = resolve_config(environ={"COBALT_PROVIDER": name, preset.key_name: "secret"})
                self.assertEqual(config.model, preset.fast_model)
                self.assertEqual(config.base_url, preset.base_url)
                self.assertEqual(config.api_key, "secret")
                self.assertIsInstance(from_config(config), AnthropicMessages if name == "anthropic" else ChatCompletions)

    def test_file_process_and_cli_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("COBALT_PROVIDER=deepseek\nDEEPSEEK_API_KEY=file-key\nCOBALT_MODEL_TIER=pro\n", encoding="utf-8")
            config = resolve_config(env_file=path, environ={"DEEPSEEK_API_KEY": "process-key"}, model="exact")
        self.assertEqual(config.api_key, "process-key")
        self.assertEqual(config.model, "exact")

    def test_missing_key_and_unsupported_model_fail_before_network(self):
        with self.assertRaisesRegex(ValueError, "ANTHROPIC_API_KEY"):
            resolve_config(environ={"COBALT_PROVIDER": "anthropic"})
        with self.assertRaisesRegex(ValueError, "Responses API"):
            resolve_config(environ={"OPENAI_API_KEY": "secret"}, provider="openai", model="gpt-6-astra")

    def test_env_parser_does_not_execute_shell_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("# note\nexport DEEPSEEK_API_KEY='literal $(whoami)'\n", encoding="utf-8")
            self.assertEqual(read_env_file(path)["DEEPSEEK_API_KEY"], "literal $(whoami)")


class ProviderAdapterTests(unittest.TestCase):
    def test_provider_object_arguments_are_accepted_and_refusal_fails_closed(self):
        reply = {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "id", "function": {"name": "read_file", "arguments": {"path": "a.py"}}}
        ]}}]}
        adapter = ChatCompletions(api_key="fake", model="glm-5.3", base_url="https://api.z.ai/api/paas/v4", provider="zai")
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(reply).encode())):
            turn = adapter.complete([{"role": "user", "content": "read"}], [])
        self.assertEqual(turn.calls[0].arguments, {"path": "a.py"})
        reply["choices"][0]["finish_reason"] = "content_filter"
        with (
            patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(reply).encode())),
            self.assertRaises(ModelOutputError),
        ):
            adapter.complete([{"role": "user", "content": "read"}], [])

    def test_openai_chat_tools_disable_reasoning(self):
        response = {"choices": [{"finish_reason": "stop", "message": {"content": "done"}}]}
        captured = {}

        def fake_open(request, timeout):
            captured["body"] = json.loads(request.data)
            return io.BytesIO(json.dumps(response).encode())

        with patch("urllib.request.urlopen", fake_open):
            ChatCompletions(api_key="fake", model="gpt-6-luna", base_url="https://api.openai.com/v1", provider="openai").complete(
                [{"role": "user", "content": "hi"}], []
            )
        self.assertEqual(captured["body"]["reasoning_effort"], "none")

    def test_anthropic_converts_complete_tool_round_trip(self):
        response = {"stop_reason": "tool_use", "content": [
            {"type": "text", "text": "Checking"},
            {"type": "tool_use", "id": "next", "name": "read_file", "input": {"path": "b.py"}},
        ], "usage": {"input_tokens": 25, "output_tokens": 8}}
        captured = {}

        def fake_open(request, timeout):
            captured["body"] = json.loads(request.data)
            captured["key"] = request.get_header("X-api-key")
            return io.BytesIO(json.dumps(response).encode())

        messages = [
            {"role": "system", "content": "Follow rules"},
            {"role": "user", "content": "Inspect"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "old", "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}]},
            {"role": "tool", "tool_call_id": "old", "content": "contents"},
        ]
        tools = [{"function": {"name": "read_file", "description": "Read", "parameters": {"type": "object"}}}]
        with patch("urllib.request.urlopen", fake_open):
            turn = AnthropicMessages(api_key="fake", model="claude-sonnet-5", base_url="https://api.anthropic.com/v1").complete(messages, tools)
        self.assertEqual(captured["body"]["system"], "Follow rules")
        self.assertEqual(captured["body"]["messages"][-1]["content"][0]["tool_use_id"], "old")
        self.assertEqual(captured["body"]["tools"][0]["input_schema"], {"type": "object"})
        self.assertEqual(captured["key"], "fake")
        self.assertEqual(turn.calls[0].arguments, {"path": "b.py"})
        self.assertEqual(turn.prompt_tokens, 25)
