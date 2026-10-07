"""LLM access with provider fallback. Groq is tried first (fast, generous free
tier), then Gemini. A provider only takes part if its API key is configured,
so the app starts and degrades gracefully with either key missing."""
import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
GROQ_PREFERRED_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]
TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "90"))

PROVIDERS = ["groq", "gemini"]
_KEY_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}

# What a user is allowed to pick in the UI, mapped to (provider, model).
# A deliberately small, explicit list — never pass a user-supplied model
# string straight to an API call.
AVAILABLE_MODELS = {
    "auto": None,  # default fallback chain, no forcing
    "groq-120b": ("groq", "openai/gpt-oss-120b"),
    "groq-20b": ("groq", "openai/gpt-oss-20b"),
    "gemini": ("gemini", GEMINI_MODEL),
}


def _pretty_gemini(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("-"))  # gemini-3.8-flash -> Gemini 3.8 Flash


MODEL_LABELS = {
    "auto": "Auto (best available)",
    "groq-120b": "GPT-OSS 120B · Groq",
    "groq-20b": "GPT-OSS 20B · Groq (fastest)",
    "gemini": f"{_pretty_gemini(GEMINI_MODEL)} · Google",
}


def provider_configured(provider: str) -> bool:
    return bool(os.environ.get(_KEY_ENV[provider]))


def configured_providers() -> list:
    return [p for p in PROVIDERS if provider_configured(p)]


def model_catalog() -> list:
    any_provider = bool(configured_providers())
    return [{
        "id": key,
        "label": MODEL_LABELS[key],
        "available": any_provider if target is None else provider_configured(target[0]),
    } for key, target in AVAILABLE_MODELS.items()]


def fast_model_choice() -> str:
    """Model for small internal tasks (rewriting a follow-up, suggesting
    search terms): the fastest one that's actually configured."""
    return "groq-20b" if provider_configured("groq") else "auto"


_clients = {}


def _groq_client():
    if "groq" not in _clients:
        from openai import OpenAI
        _clients["groq"] = OpenAI(
            api_key=os.environ["GROQ_API_KEY"],
            base_url="https://api.groq.com/openai/v1",
            timeout=TIMEOUT_SECONDS,
            max_retries=1,
        )
    return _clients["groq"]


def _gemini_client():
    if "gemini" not in _clients:
        from google import genai
        from google.genai import types
        _clients["gemini"] = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=int(TIMEOUT_SECONDS * 1000)),
        )
    return _clients["gemini"]


_groq_model_cache = None


def _resolve_groq_model(preferred: str = None) -> str:
    global _groq_model_cache
    if preferred:
        return preferred  # caller asked for a specific model explicitly
    if _groq_model_cache:
        return _groq_model_cache

    resp = requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
        timeout=10,
    )
    resp.raise_for_status()
    available = {m["id"] for m in resp.json()["data"]}

    for candidate in GROQ_PREFERRED_MODELS:
        if candidate in available:
            _groq_model_cache = candidate
            return candidate

    fallback = next(iter(available))
    log.warning("No preferred Groq model available, using '%s' instead", fallback)
    _groq_model_cache = fallback
    return fallback


def _resolve(provider: str, model: str = None) -> str:
    if not provider_configured(provider):
        raise RuntimeError(f"{provider} is not configured (set {_KEY_ENV[provider]})")
    if provider == "groq":
        return _resolve_groq_model(model)
    return model or GEMINI_MODEL


def _call_gemini(prompt: str, model: str) -> str:
    response = _gemini_client().models.generate_content(model=model, contents=prompt)
    return response.text


def _call_groq(prompt: str, model: str) -> str:
    response = _groq_client().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _stream_gemini(prompt: str, model: str):
    for chunk in _gemini_client().models.generate_content_stream(model=model, contents=prompt):
        if chunk.text:
            yield chunk.text


def _stream_groq(prompt: str, model: str):
    stream = _groq_client().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


CALL_FUNCTIONS = {"gemini": _call_gemini, "groq": _call_groq}
STREAM_FUNCTIONS = {"gemini": _stream_gemini, "groq": _stream_groq}


def _candidates(model_choice: str) -> list:
    """model_choice is a key into AVAILABLE_MODELS. 'auto' (default) uses the
    normal fallback chain. Any other valid key forces that one provider/model
    with no fallback - if the user explicitly picked it, we honor that choice
    rather than silently substituting something else."""
    forced = AVAILABLE_MODELS.get(model_choice)
    if forced:
        return [forced]
    candidates = [(p, None) for p in configured_providers()]
    if not candidates:
        raise RuntimeError("No LLM provider is configured - set GROQ_API_KEY and/or GEMINI_API_KEY")
    if provider_configured("groq"):
        # Groq rate-limits each model separately (the free tier allows ~2
        # questions a minute on the 120B model), so the smaller model is a
        # useful last resort when both other options are busy.
        candidates.append(("groq", "openai/gpt-oss-20b"))
    return candidates


def generate_full(prompt: str, model_choice: str = "auto") -> tuple:
    """Returns (text, provider, model)."""
    candidates = _candidates(model_choice)
    last_error = None
    for provider, model in candidates:
        try:
            resolved = _resolve(provider, model)
            return CALL_FUNCTIONS[provider](prompt, resolved), provider, resolved
        except Exception as e:
            if len(candidates) == 1 and model_choice != "auto":
                raise RuntimeError(f"{provider} ({model}) failed: {e}") from e
            log.warning("%s failed: %s", provider, e)
            last_error = e
    raise RuntimeError(f"All providers failed. Last error: {last_error}")


def generate(prompt: str, model_choice: str = "auto") -> tuple:
    """Returns (text, provider)."""
    text, provider, _ = generate_full(prompt, model_choice)
    return text, provider


def stream(prompt: str, model_choice: str = "auto") -> tuple:
    """Returns (provider, model, token_iterator). The first token is fetched
    before returning, so in 'auto' mode a provider that fails up front (bad
    key, quota, outage) is skipped in favour of the next one. A failure after
    tokens have started flowing is raised to the caller."""
    candidates = _candidates(model_choice)
    last_error = None
    for provider, model in candidates:
        try:
            resolved = _resolve(provider, model)
            tokens = STREAM_FUNCTIONS[provider](prompt, resolved)
            first = next(tokens, "")
        except Exception as e:
            if len(candidates) == 1 and model_choice != "auto":
                raise RuntimeError(f"{provider} ({model}) failed: {e}") from e
            log.warning("%s failed: %s", provider, e)
            last_error = e
            continue

        def chained(first=first, rest=tokens):
            if first:
                yield first
            yield from rest

        return provider, resolved, chained()
    raise RuntimeError(f"All providers failed. Last error: {last_error}")
