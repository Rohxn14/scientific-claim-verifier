import pytest

import generation


@pytest.fixture
def keys(monkeypatch):
    def set_keys(groq=True, gemini=True):
        for env, on in (("GROQ_API_KEY", groq), ("GEMINI_API_KEY", gemini)):
            if on:
                monkeypatch.setenv(env, "test-key")
            else:
                monkeypatch.delenv(env, raising=False)
    return set_keys


def test_auto_tries_groq_then_gemini_then_the_small_groq_model(keys):
    keys(groq=True, gemini=True)
    assert generation._candidates("auto") == [("groq", None), ("gemini", None), ("groq", "openai/gpt-oss-20b")]


def test_auto_skips_unconfigured_providers(keys):
    keys(groq=False, gemini=True)
    assert generation._candidates("auto") == [("gemini", None)]


def test_no_provider_configured_is_a_clear_error(keys):
    keys(groq=False, gemini=False)
    with pytest.raises(RuntimeError, match="No LLM provider"):
        generation._candidates("auto")


def test_explicit_model_choice_has_no_fallback(keys):
    keys()
    assert generation._candidates("gemini") == [("gemini", generation.GEMINI_MODEL)]


def test_stream_falls_back_when_first_provider_fails_before_any_token(keys, monkeypatch):
    keys()
    monkeypatch.setattr(generation, "_resolve", lambda provider, model=None: model or f"{provider}-default")

    def broken(prompt, model):
        raise RuntimeError("429 rate limit")
        yield  # pragma: no cover - makes this a generator

    def working(prompt, model):
        yield "Hello "
        yield "world"

    monkeypatch.setitem(generation.STREAM_FUNCTIONS, "groq", broken)
    monkeypatch.setitem(generation.STREAM_FUNCTIONS, "gemini", working)
    provider, model, tokens = generation.stream("prompt")
    assert (provider, "".join(tokens)) == ("gemini", "Hello world")


def test_model_catalog_marks_unconfigured_models_unavailable(keys):
    keys(groq=True, gemini=False)
    available = {m["id"]: m["available"] for m in generation.model_catalog()}
    assert available == {"auto": True, "groq-120b": True, "groq-20b": True, "gemini": False}
