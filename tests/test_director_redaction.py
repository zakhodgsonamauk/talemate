"""Unit tests for the director chat nospoilers redaction gate.

Covers:
- `_chat_build_director_message`: applies the gate only in nospoilers mode,
  stores both texts when the gate rewrites, stores a plain message when the
  gate is a no-op or the chat is in another mode.
- `chat_history_for_prompt`: the director's own prompt history prefers the
  unredacted text; the stored (displayed) message stays redacted.
- `chat_redact_message`: extracts the <REDACTED> tag from the LLM response
  and falls back to the original text on empty responses and exceptions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from conftest import MockScene, bootstrap_scene

import talemate.instance as instance
from _director_test_helpers import patch_prompt_request_in


ORIGINAL = "We're mid-Beat 3 - the broker is the pivot. Want the raid early or late?"
REDACTED = "On it - something is about to shift. Stay sharp."


@pytest.fixture
def scene():
    s = MockScene()
    bootstrap_scene(s)
    return s


@pytest.fixture
def director(scene):
    return instance.get_agent("director")


@pytest.fixture
def nospoilers_chat(director):
    chat = director.chat_create()
    chat.mode = "nospoilers"
    director._chat_save(chat)
    return chat


class TestBuildDirectorMessage:
    @pytest.mark.asyncio
    async def test_nospoilers_stores_both_texts(self, director, nospoilers_chat):
        director.chat_redact_message = AsyncMock(return_value=REDACTED)

        message = await director._chat_build_director_message(
            nospoilers_chat.id, ORIGINAL
        )

        director.chat_redact_message.assert_awaited_once()
        assert message.message == REDACTED
        assert message.unredacted_message == ORIGINAL

    @pytest.mark.asyncio
    async def test_normal_mode_skips_gate(self, director):
        chat = director.chat_create()
        director.chat_redact_message = AsyncMock(return_value=REDACTED)

        message = await director._chat_build_director_message(chat.id, ORIGINAL)

        director.chat_redact_message.assert_not_awaited()
        assert message.message == ORIGINAL
        assert message.unredacted_message is None

    @pytest.mark.asyncio
    async def test_noop_redaction_stores_plain_message(
        self, director, nospoilers_chat
    ):
        director.chat_redact_message = AsyncMock(return_value=ORIGINAL)

        message = await director._chat_build_director_message(
            nospoilers_chat.id, ORIGINAL
        )

        assert message.message == ORIGINAL
        assert message.unredacted_message is None


class TestPromptHistoryPrefersUnredacted:
    @pytest.mark.asyncio
    async def test_append_path_and_history_serialization(
        self, director, nospoilers_chat
    ):
        director.chat_redact_message = AsyncMock(return_value=REDACTED)

        message = await director._chat_build_director_message(
            nospoilers_chat.id, ORIGINAL
        )
        await director.chat_append_message(nospoilers_chat.id, message)

        # stored (displayed) message carries both texts
        stored = director.chat_get(nospoilers_chat.id).messages[-1]
        assert stored.message == REDACTED
        assert stored.unredacted_message == ORIGINAL

        # the director's own prompt history sees the unredacted text
        history = director.chat_history_for_prompt(nospoilers_chat.id)
        assert history[-1].message == ORIGINAL

        # serialization for the prompt must not mutate the stored message
        stored_after = director.chat_get(nospoilers_chat.id).messages[-1]
        assert stored_after.message == REDACTED


class TestChatRedactMessage:
    @pytest.mark.asyncio
    async def test_extracts_redacted_tag(
        self, director, nospoilers_chat, monkeypatch
    ):
        install = patch_prompt_request_in(monkeypatch)
        stub = install(
            {
                "director.redact-spoilers": [
                    (f"<REDACTED>{REDACTED}</REDACTED>", {}),
                ]
            }
        )

        result = await director.chat_redact_message(nospoilers_chat, ORIGINAL)

        assert result == REDACTED
        assert stub.calls[0]["template"] == "director.redact-spoilers"
        assert stub.calls[0]["vars"]["draft"] == ORIGINAL

    @pytest.mark.asyncio
    async def test_prefill_coerced_response_without_tags(
        self, director, nospoilers_chat, monkeypatch
    ):
        """The template prefills "<REDACTED>", so Prompt.request prepends the
        opening tag when the model does not repeat it. A completion without
        any tags is still the prompt-conditioned rewrite and must be used as
        the redacted text (never fall back to the unredacted original)."""
        install = patch_prompt_request_in(monkeypatch)
        install(
            {
                # what Prompt.request returns after prepending the prefill
                "director.redact-spoilers": [
                    (f"<REDACTED>{REDACTED}", {}),
                ]
            }
        )

        result = await director.chat_redact_message(nospoilers_chat, ORIGINAL)

        assert result == REDACTED

    @pytest.mark.asyncio
    async def test_empty_response_returns_original(
        self, director, nospoilers_chat, monkeypatch
    ):
        install = patch_prompt_request_in(monkeypatch)
        install({})  # no queued responses -> Prompt.request returns ("", {})

        result = await director.chat_redact_message(nospoilers_chat, ORIGINAL)

        assert result == ORIGINAL

    @pytest.mark.asyncio
    async def test_exception_returns_original(
        self, director, nospoilers_chat, monkeypatch
    ):
        from talemate.prompts.base import Prompt

        async def _boom(cls, uid, client, kind, vars=None, **kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(Prompt, "request", classmethod(_boom), raising=True)

        result = await director.chat_redact_message(nospoilers_chat, ORIGINAL)

        assert result == ORIGINAL
