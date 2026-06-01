"""Provider registry — PRD §11.1 as Python data.

This is the single source of truth for what providers, auth flows, models,
and metadata the picker, doctor, and model screens all read from. When real
ProviderTransport classes land, this table stays — only the `transport` field
gets pointed at a real implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Model:
    id: str
    description: str
    badge: str = ""  # short tag, e.g. "tool-calling", "cheap", "long ctx"


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    blurb: str  # one-line subtitle shown in the picker
    auth: str  # "api_key" | "device_oauth" | "local"
    env_var: str
    base_url: str
    wire: str  # "openai-compatible" | "anthropic-native" | "google-native"
    models: list[Model] = field(default_factory=list)
    free_tier: bool = False


PROVIDERS: list[Provider] = [
    Provider(
        id="groq",
        label="Groq",
        blurb="fastest free tier (recommended to start)",
        auth="api_key",
        env_var="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        wire="openai-compatible",
        free_tier=True,
        models=[
            Model("llama-3.3-70b-versatile", "best general-purpose, tool-calling", "default"),
            Model("llama-3.1-8b-instant", "fastest, cheapest, good for quick tasks", "cheap"),
            Model("mixtral-8x7b-32768", "long context (32k)", "long ctx"),
            Model("whisper-large-v3", "speech-to-text only", "STT"),
        ],
    ),
    Provider(
        id="openai",
        label="OpenAI",
        blurb="GPT-4o, GPT-4.1, o-series, embeddings",
        auth="api_key",
        env_var="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
        wire="openai-native",
        models=[
            Model("gpt-4o", "vision + tool-calling, fast", "vision"),
            Model("gpt-4o-mini", "cheap, capable, vision-aware", "cheap"),
            Model("o4-mini", "reasoning model, slower"),
            Model("gpt-4.1", "long-form generation"),
        ],
    ),
    Provider(
        id="anthropic",
        label="Anthropic",
        blurb="Claude Opus, Sonnet, Haiku; prompt caching",
        auth="api_key",
        env_var="ANTHROPIC_API_KEY",
        base_url="https://api.anthropic.com",
        wire="anthropic-native",
        models=[
            Model("claude-opus-4-7", "strongest writing & code", "default"),
            Model("claude-sonnet-4-6", "balanced quality/speed"),
            Model("claude-haiku-4-5", "fastest Claude", "cheap"),
        ],
    ),
    Provider(
        id="openrouter",
        label="OpenRouter",
        blurb="200+ models via one key, fallback routing",
        auth="api_key",
        env_var="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        wire="openai-compatible",
        models=[
            Model("anthropic/claude-opus-4-7", "Claude through OpenRouter"),
            Model("meta-llama/llama-3.3-70b-instruct", "Llama at low cost"),
            Model("google/gemini-2.0-flash-001", "Gemini via OpenRouter"),
        ],
    ),
    Provider(
        id="gemini",
        label="Google Gemini",
        blurb="Gemini 1.5 / 2.x models, vision-capable",
        auth="api_key",
        env_var="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        wire="google-native",
        models=[
            Model("gemini-2.0-flash", "fast, multimodal"),
            Model("gemini-1.5-pro", "long context"),
        ],
    ),
    Provider(
        id="deepseek",
        label="DeepSeek",
        blurb="DeepSeek-V3, DeepSeek-R1",
        auth="api_key",
        env_var="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        wire="openai-compatible",
        models=[
            Model("deepseek-chat", "general-purpose V3"),
            Model("deepseek-reasoner", "R1 reasoning model"),
        ],
    ),
    Provider(
        id="mistral",
        label="Mistral AI",
        blurb="Mistral Large, Codestral, Voxtral; EU-hosted; OpenAI-compatible",
        auth="api_key",
        env_var="MISTRAL_API_KEY",
        base_url="https://api.mistral.ai/v1",
        wire="openai-compatible",
        models=[
            Model("mistral-large-latest",  "flagship — best reasoning + tool use", "default"),
            Model("mistral-medium-latest", "balanced quality / cost"),
            Model("mistral-small-latest",  "fast, cheap, capable", "cheap"),
            Model("codestral-latest",      "code-tuned (fill-in-middle)",      "code"),
            Model("pixtral-large-latest",  "vision-capable",                   "vision"),
            Model("voxtral-mini-2507",     "speech-to-text + TTS",             "voice"),
            Model("mistral-embed",         "embeddings",                       "embed"),
        ],
    ),
    Provider(
        id="huggingface",
        label="Hugging Face",
        blurb="Inference providers (Groq, Together, SambaNova fallbacks)",
        auth="api_key",
        env_var="HF_TOKEN",
        base_url="https://router.huggingface.co/v1",
        wire="openai-compatible",
        models=[
            Model("meta-llama/Llama-3.3-70B-Instruct", "via best available provider"),
            Model("Qwen/Qwen2.5-72B-Instruct", "Qwen 72B"),
        ],
    ),
    Provider(
        id="ollama",
        label="Ollama / local",
        blurb="auto-detects localhost:11434, runs on-device",
        auth="local",
        env_var="OLLAMA_HOST",
        base_url="http://localhost:11434/v1",
        wire="openai-compatible",
        free_tier=True,
        models=[
            Model("llama3:8b", "general-purpose local"),
            Model("qwen2.5:14b", "stronger local"),
            Model("mistral:7b", "tiny, fast"),
        ],
    ),
    Provider(
        id="custom",
        label="Custom endpoint",
        blurb="any OpenAI-compatible base URL (vLLM, LM Studio, LiteLLM…)",
        auth="api_key",
        env_var="ARGUS_CUSTOM_BASE_URL",
        base_url="",
        wire="openai-compatible",
        models=[Model("<your-model>", "as exposed by your endpoint")],
    ),
]

BY_ID: dict[str, Provider] = {p.id: p for p in PROVIDERS}


def get_provider(provider_id: str) -> Provider:
    return BY_ID[provider_id]
