from backend.models import _is_chat_model

# Snapshot of the live Kyma catalog (116 models) observed during development --
# a real regression check that the keyword filter classifies each one right,
# not just a synthetic example.
_NON_CHAT_IDS = [
    "all-minilm-l12", "all-minilm-l6", "bge-base-en", "bge-large-en", "bge-m3",
    "eleven-flash-v2-5", "eleven-multilingual-v2", "eleven-turbo-v2-5", "eleven-v3",
    "elevenlabs-music", "elevenlabs-sfx", "embeddinggemma-300m",
    "flux-1.1-ultra", "flux-2-pro", "flux-kontext-pro",
    "gemini-2.5-flash-native-audio-preview-12-2025", "gemini-3-flash-audio",
    "gemini-3.1-flash-live-preview", "gemini-3.5-live-translate-preview",
    "gpt-4o-mini-transcribe-2025-12-15", "gpt-image-2", "gpt-realtime-translate",
    "gte-base", "hailuo-02-1080p", "hailuo-02-512p", "hailuo-02-768p",
    "ideogram-v3", "imagen-4", "imagen-4-fast", "imagen-4-ultra",
    "kling-2.5-pro", "kling-3-pro", "kling-3-pro-audio",
    "minimax-image-01", "minimax-music", "minimax-music-pro",
    "minimax-speech-hd", "minimax-speech-turbo", "minimax-voice-clone", "minimax-voice-design",
    "multilingual-e5-large", "muse-spark-1.1", "nano-banana", "nano-banana-3-flash",
    "qwen3-embedding-0.6b", "qwen3-embedding-4b", "qwen3-embedding-8b", "qwen3-reranker-8b",
    "recraft-v3", "recraft-v4", "recraft-v4-pro", "recraft-v4-vector", "recraft-v4-vector-pro",
    "seedance-2-fast", "seedance-2-pro", "veo-3", "veo-3-fast", "whisper-v3-turbo",
]

_CHAT_IDS = [
    "claude-fable-5", "claude-haiku-4-5", "claude-opus-4-7", "claude-opus-5",
    "claude-opus-5-fast", "claude-sonnet-4-6", "claude-sonnet-5",
    "deepseek-r1", "deepseek-v3", "deepseek-v4-flash", "deepseek-v4-pro",
    "gemini-2.5-flash", "gemini-3-flash", "gemini-3.1-pro", "gemini-3.5-flash",
    "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.7-flash", "gemma-4-31b",
    "glm-4.5-air", "glm-4.7-flash", "glm-5.1", "glm-5.2",
    "gpt-5.6-luna", "gpt-5.6-luna-pro", "gpt-5.6-sol", "gpt-5.6-sol-pro",
    "gpt-5.6-terra", "gpt-5.6-terra-pro", "gpt-oss-120b",
    "grok-4.20", "grok-4.20-multi-agent", "grok-4.3", "grok-4.5", "grok-4.6", "grok-build",
    "hermes-3-405b", "hermes-3-70b", "kimi-k2.5", "kimi-k2.6", "kimi-k2.7-code", "kimi-k3",
    "llama-3.3-70b", "llama-4-maverick", "minimax-m2.5", "minimax-m2.7", "minimax-m3",
    "nemotron-3-ultra-550b", "qwen-3-32b", "qwen-3-coder", "qwen-3.6-plus", "qwen-3.7-max",
    "qwen-3.7-plus", "qwen-3.8-max", "qwen3.7-flash", "sonar", "sonar-pro", "step-3.7-flash",
]


def test_filters_out_non_chat_models():
    for model_id in _NON_CHAT_IDS:
        assert not _is_chat_model(model_id), f"{model_id} should be filtered out"


def test_keeps_chat_models():
    for model_id in _CHAT_IDS:
        assert _is_chat_model(model_id), f"{model_id} should be kept"
