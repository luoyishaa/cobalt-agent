# Model providers

Cobalt keeps provider selection at the model boundary. The agent runtime uses one
`complete(messages, tools)` interface; provider adapters return a common structured
turn. A preset contains an endpoint, API key variable, two model IDs, and protocol.
Adding an entry to the preset table is appropriate only when the provider supports
the required client-side tool loop. Provider compatibility is not an evaluation of
coding quality; measure that with the benchmark cases before comparing models.

## Configure

Copy `.env.example` to `.env` in the directory from which you run `cobalt`.
Fill the selected provider's key and set `COBALT_PROVIDER`. The default is
DeepSeek, so its key alone is enough for a first run. `COBALT_MODEL_TIER` is
`fast` or `pro`. `COBALT_MODEL_ID` overrides the preset model. `COBALT_BASE_URL`
overrides its HTTPS endpoint, for example for an Alibaba Cloud workspace domain.

The order of precedence is CLI flags, process environment, then `.env`, then
defaults. `--env-file` points to a different file. Cobalt never loads credentials
from the repository passed to `--workspace`; that repository may be untrusted.
Missing keys and incompatible model selections fail before a network call.
The active provider receives the task text and tool outputs over its API.

## Presets and limits

| Provider | Key variable | Protocol | Notes |
| --- | --- | --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY` | Chat | Default; live tool call smoke tested. |
| Z.ai | `ZAI_API_KEY` | Chat | GLM 5.3 Flash / 5.3. |
| Qwen | `DASHSCOPE_API_KEY` | Chat | Default legacy Beijing endpoint still works; set `COBALT_BASE_URL` to your workspace domain for the recommended dedicated endpoint. |
| Kimi | `MOONSHOT_API_KEY` | Chat | Uses the current international Moonshot API domain. |
| OpenAI | `OPENAI_API_KEY` | Chat | GPT-6 Luna / Sol tools require `reasoning_effort=none`; Astra requires Responses and is rejected. |
| Gemini | `GEMINI_API_KEY` | Chat compatibility | Supports client function calls; this route does not cover all native Gemini features. |
| Anthropic | `ANTHROPIC_API_KEY` | Messages | Converts tool calls and results to native content blocks. |
| Groq | `GROQ_API_KEY` | Chat compatibility | Llama 3.1 8B / 3.3 70B; model access depends on account. |
| Mistral | `MISTRAL_API_KEY` | Chat | Small / Medium latest aliases. |
| xAI | `XAI_API_KEY` | Chat | Grok 4.7. |
| OpenRouter | `OPENROUTER_API_KEY` | Chat compatibility | GPT-OSS 120B default; router may use different underlying hosts. |
| SiliconFlow | `SILICONFLOW_API_KEY` | Chat compatibility | DeepSeek V4 Flash / Pro V4. |
| MiniMax | `MINIMAX_API_KEY` | Chat compatibility | M2.7 / M3. |

Model IDs can change or require separate account access. If an account does not
expose a preset model, set `COBALT_MODEL_ID` to a tool-capable model that it does
expose. Only DeepSeek has been exercised with a real key in this repository;
all provider presets are checked without network access. The shared Chat adapter
and Claude adapter are checked with fake HTTP responses, including a complete
Claude tool round trip. Passing these tests does not establish live availability
or benchmark quality for another provider.

## Official protocol references

- [DeepSeek tool calls](https://api-docs.deepseek.com/guides/tool_calls/)
- [Z.ai Chat Completion](https://docs.z.ai/api-reference/llm/chat-completion)
- [Alibaba Cloud OpenAI-compatible Chat](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)
- [Kimi Chat Completions](https://platform.kimi.ai/docs/api/chat)
- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
- [Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
- [Groq local tool calling](https://console.groq.com/docs/tool-use/local-tool-calling)
- [Mistral function calling](https://docs.mistral.ai/studio/conversations/function-calling)
- [xAI function calling](https://docs.x.ai/developers/tools/function-calling)
- [OpenRouter tool calling](https://openrouter.ai/docs/guides/features/tool-calling)
- [SiliconFlow Chat API](https://docs.siliconflow.cn/docs/api/chat-completions-post)
- [MiniMax Chat Completions](https://platform.minimax.io/docs/api-reference/text-chat-openai)
