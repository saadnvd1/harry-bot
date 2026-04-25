"""Tests for brain/router.py — message routing + complexity classification."""

import pytest
from brain.router import route, Route, classify_complexity


class TestExplicitPrefixes:
    """Test explicit agent/model prefix routing."""

    def test_haiku_prefix(self):
        r = route("!h what's the weather?")
        assert r.agent == "claude"
        assert r.model == "claude-haiku-4-5-20251001"
        assert r.message == "what's the weather?"
        assert "prefix" in r.reason

    def test_sonnet_prefix(self):
        r = route("!s help me think through this")
        assert r.agent == "claude"
        assert r.model == "claude-sonnet-4-6"
        assert r.message == "help me think through this"

    def test_opus_prefix(self):
        r = route("!opus analyze this deeply")
        assert r.agent == "claude"
        assert r.model == "claude-opus-4-7"

    def test_ollama_prefix(self):
        r = route("!ollama quick question")
        assert r.agent == "ollama"
        assert r.model is None

    def test_free_prefix_aliases_ollama(self):
        r = route("!free what time is it")
        assert r.agent == "ollama"

    def test_local_prefix_aliases_ollama(self):
        r = route("!local test")
        assert r.agent == "ollama"

    def test_codex_prefix(self):
        r = route("!codex help me code")
        assert r.agent == "codex"

    def test_opencode_prefix(self):
        r = route("!opencode do something")
        assert r.agent == "opencode"

    def test_generic_prefix_with_model(self):
        r = route("!ollama:llama3 test message")
        assert r.agent == "ollama"
        assert r.model == "llama3"
        assert r.message == "test message"


class TestComplexityClassification:
    """Test deterministic complexity classification."""

    # --- Simple ---

    def test_math_simple(self):
        assert classify_complexity("what's 5 + 3") == "simple"
        assert classify_complexity("What is 100 * 50?") == "simple"

    def test_conversion_simple(self):
        assert classify_complexity("convert 5 miles to km") == "simple"

    def test_time_simple(self):
        assert classify_complexity("what's the time") == "simple"
        assert classify_complexity("what time is it") == "simple"
        assert classify_complexity("what is the date") == "simple"

    def test_definitions_simple(self):
        assert classify_complexity("define ephemeral") == "simple"
        assert classify_complexity("spell necessary") == "simple"

    def test_acks_simple(self):
        assert classify_complexity("ok") == "simple"
        assert classify_complexity("thanks") == "simple"
        assert classify_complexity("yes") == "simple"
        assert classify_complexity("lol") == "simple"

    def test_short_empty_simple(self):
        assert classify_complexity("hi") == "simple"
        assert classify_complexity("k") == "simple"

    # --- Complex ---

    def test_architecture_complex(self):
        assert classify_complexity("help me design a system architecture") == "complex"
        assert classify_complexity("let's think through the data model") == "complex"

    def test_refactor_complex(self):
        assert classify_complexity("can you refactor the auth module") == "complex"
        assert classify_complexity("we need to redesign the API") == "complex"

    def test_analysis_complex(self):
        assert classify_complexity("help me think through this problem") == "complex"
        assert classify_complexity("analyze the performance bottleneck") == "complex"

    def test_multi_question_complex(self):
        msg = "What's the best approach? Should we use Redis? How does it scale?"
        assert classify_complexity(msg) == "complex"

    def test_long_message_complex(self):
        assert classify_complexity("x" * 1100) == "complex"

    # --- Medium ---

    def test_normal_question_medium(self):
        assert classify_complexity("add a button to the sidebar") == "medium"
        assert classify_complexity("hey how are you") == "medium"

    def test_short_question_medium(self):
        assert classify_complexity("what do you think about X") == "medium"


class TestDefaultRouting:
    """Test default routing behavior."""

    def test_trivial_routes_to_ollama(self):
        r = route("what's 5 + 5")
        assert r.agent == "ollama"
        assert r.complexity == "simple"

    def test_short_ack_routes_to_gemini(self):
        r = route("thanks")
        assert r.agent == "gemini"
        assert r.complexity == "simple"

    def test_medium_routes_to_sonnet(self):
        r = route("add a button to the sidebar")
        assert r.agent == "claude"
        assert r.model is None  # sonnet is CLI default
        assert r.complexity == "medium"

    def test_complex_routes_to_opus(self):
        r = route("help me design a system architecture")
        assert r.agent == "claude"
        assert r.model == "claude-opus-4-7"
        assert r.complexity == "complex"


class TestRouteDataclass:
    """Test Route dataclass structure."""

    def test_route_has_all_fields(self):
        r = route("test message")
        assert hasattr(r, "agent")
        assert hasattr(r, "model")
        assert hasattr(r, "message")
        assert hasattr(r, "reason")
        assert hasattr(r, "complexity")

    def test_message_preserved(self):
        original = "this is a normal question about something"
        r = route(original)
        assert r.message == original

    def test_complexity_always_set(self):
        for msg in ["ok", "add a feature", "design the system"]:
            r = route(msg)
            assert r.complexity in ("simple", "medium", "complex")
