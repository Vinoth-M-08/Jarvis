# tests/test_llm.py
from unittest.mock import patch, MagicMock

OLLAMA_CONFIG = {
    "llm": {
        "active_provider": "ollama",
        "temperature": 0.7,
        "providers": {
            "ollama": {"type": "ollama", "model": "qwen3:4b", "base_url": "http://localhost:11434"},
        },
    }
}

OPENAI_CONFIG = {
    "llm": {
        "active_provider": "lmstudio",
        "temperature": 0.7,
        "providers": {
            "lmstudio": {"type": "openai", "model": "local-model", "base_url": "http://localhost:1234/v1"},
            "ollama": {"type": "ollama", "model": "qwen3:4b", "base_url": "http://localhost:11434"},
        },
    }
}


def _mock_openai_response(content="response"):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    return resp


def _mock_ollama_response(content="response"):
    return MagicMock(message=MagicMock(content=content))


def test_chat_returns_string_from_ollama():
    """chat() should return the Ollama reply when Ollama is the active provider."""
    with patch("jarvis.llm._load_config", return_value=OLLAMA_CONFIG), \
         patch("ollama.chat", return_value=_mock_ollama_response("Good morning, sir.")):
        from jarvis.llm import chat
        result = chat([{"role": "user", "content": "Hello"}])
        assert isinstance(result, str)
        assert "Good morning" in result


def test_chat_disables_thinking_by_default():
    """chat() should pass think=False to Ollama when the config doesn't set it."""
    with patch("jarvis.llm._load_config", return_value=OLLAMA_CONFIG), \
         patch("ollama.chat", return_value=_mock_ollama_response()) as mock_chat:
        from jarvis.llm import chat
        chat([{"role": "user", "content": "test"}])
        assert mock_chat.call_args.kwargs["think"] is False


def test_chat_respects_think_setting():
    """chat() should pass the provider's think setting through to Ollama."""
    cfg = {"llm": {**OLLAMA_CONFIG["llm"], "providers": {
        "ollama": {**OLLAMA_CONFIG["llm"]["providers"]["ollama"], "think": True},
    }}}
    with patch("jarvis.llm._load_config", return_value=cfg), \
         patch("ollama.chat", return_value=_mock_ollama_response()) as mock_chat:
        from jarvis.llm import chat
        chat([{"role": "user", "content": "test"}])
        assert mock_chat.call_args.kwargs["think"] is True


def test_chat_uses_openai_provider_when_active():
    """chat() should call the OpenAI-compatible provider when it is active."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_openai_response("from lmstudio")
    with patch("jarvis.llm._load_config", return_value=OPENAI_CONFIG), \
         patch("openai.OpenAI", return_value=mock_client), \
         patch("ollama.chat") as mock_ollama_chat:
        from jarvis.llm import chat
        result = chat([{"role": "user", "content": "test"}])
        assert result == "from lmstudio"
        mock_client.chat.completions.create.assert_called_once()
        mock_ollama_chat.assert_not_called()


def test_chat_falls_back_to_ollama():
    """chat() should fall back to Ollama when the OpenAI-compatible provider fails."""
    with patch("jarvis.llm._load_config", return_value=OPENAI_CONFIG), \
         patch("openai.OpenAI", side_effect=Exception("connection refused")), \
         patch("ollama.chat", return_value=_mock_ollama_response("from ollama")):
        from jarvis.llm import chat
        result = chat([{"role": "user", "content": "test"}])
        assert result == "from ollama"


def test_chat_returns_error_string_when_ollama_fails():
    """chat() should return an error string instead of raising when Ollama fails."""
    with patch("jarvis.llm._load_config", return_value=OLLAMA_CONFIG), \
         patch("ollama.chat", side_effect=Exception("ollama not running")):
        from jarvis.llm import chat
        result = chat([{"role": "user", "content": "test"}])
        assert result.startswith("[LLM error:")
