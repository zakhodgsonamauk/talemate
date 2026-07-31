import re
from typing import TYPE_CHECKING

import structlog

import talemate.emit.async_signals as async_signals
import talemate.util as util
from talemate.agents.base import (
    AgentAction,
    AgentActionConfig,
    set_processing,
)
from talemate.agents.context import active_agent
from talemate.prompts import Prompt

if TYPE_CHECKING:
    from talemate.agents.narrator import NarratorAgentEmission

log = structlog.get_logger("talemate.agents.director.narration_supervision")

NARRATION_PATTERN = re.compile(r"<NARRATION>(.*?)(?:</NARRATION>|$)", re.S)


class NarrationSupervisionMixin:
    """
    Director agent mixin that intercepts narrator output and either reviews
    it (pass or correct) or rewrites it wholesale, using the director's
    client and a much wider slice of scene history than the narrator saw.

    Hooks the `agent.narrator.generated` signal, so it applies to every
    narration path (director-instructed, auto-narration, manual narrate
    actions) before the message is constructed and pushed to the scene.
    """

    @classmethod
    def add_narration_supervision_actions(cls, actions: dict[str, AgentAction]):
        actions["supervise_narration"] = AgentAction(
            enabled=False,
            container=True,
            can_be_disabled=True,
            quick_toggle=True,
            experimental=True,
            label="Narration Supervision",
            icon="mdi-eye-check",
            description=(
                "Review or rewrite narrator output with the director's model and a "
                "wider context window. Targets story-level repetition, stalling and "
                "contradictions that the narrator's smaller context cannot see. "
                "Adds one director generation per narration."
            ),
            config={
                "mode": AgentActionConfig(
                    type="text",
                    label="Mode",
                    description=(
                        "Supervise: keep the narrator's text unless it repeats, stalls or "
                        "contradicts - then correct it. Author: always rewrite in the "
                        "director's own words, using the narrator's draft as intent only."
                    ),
                    value="supervise",
                    choices=[
                        {"label": "Supervise (pass or correct)", "value": "supervise"},
                        {"label": "Author (always rewrite)", "value": "author"},
                    ],
                ),
                "guidance": AgentActionConfig(
                    type="blob",
                    label="Extra guidance",
                    description="Additional review criteria for the director when evaluating narration.",
                    value="",
                ),
            },
        )

    # config property helpers

    @property
    def narration_supervision_enabled(self) -> bool:
        return self.resolve_enabled("supervise_narration")

    @property
    def narration_supervision_mode(self) -> str:
        return self.resolve_config("supervise_narration", "mode")

    @property
    def narration_supervision_guidance(self) -> str:
        return self.resolve_config("supervise_narration", "guidance")

    # signal connect

    def connect(self, scene):
        super().connect(scene)
        async_signals.get("agent.narrator.generated").connect(
            self.narration_supervision_on_narrator_generated
        )

    # handlers

    async def narration_supervision_on_narrator_generated(
        self, emission: "NarratorAgentEmission"
    ):
        """
        Runs after every narrator generation, before the narration becomes a
        scene message. Replaces emission.response when the director produces
        a revision.
        """
        if not self.enabled or not self.narration_supervision_enabled:
            return

        draft = (emission.response or "").strip()
        if not draft:
            return

        # query narrations are analysis lookups, not story text
        try:
            if active_agent.get().state.get("narrator__query_narration"):
                return
        except AttributeError:
            pass

        try:
            revised = await self.narration_supervision_process(draft)
        except Exception as e:
            log.error(
                "narration_supervision.error",
                error=e,
                mode=self.narration_supervision_mode,
            )
            return

        if revised and revised.strip() and revised.strip() != draft:
            log.debug(
                "narration_supervision.revised",
                mode=self.narration_supervision_mode,
                draft_length=len(draft),
                revised_length=len(revised),
            )
            emission.response = revised.strip()

    # actions

    @set_processing
    async def narration_supervision_process(self, draft: str) -> str | None:
        """
        Review (or rewrite) a draft narration with the director's client.

        Returns the replacement narration, or None to keep the draft.
        """
        mode = self.narration_supervision_mode
        draft_tokens = util.count_tokens(draft)
        response_length = min(max(512, draft_tokens * 2), 2048)

        response, _ = await Prompt.request(
            "director.supervise-narration",
            self.client,
            f"narrate_{response_length}",
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "draft": draft,
                "mode": mode,
                "guidance": self.narration_supervision_guidance,
                "response_length": response_length,
            },
        )

        if not response:
            return None

        match = NARRATION_PATTERN.search(response)
        if match:
            revised = match.group(1).strip()
            return revised or None

        if "PASS" in response.strip()[:40].upper():
            return None

        if mode == "author":
            # author mode must always produce narration - treat the whole
            # response as the rewrite if the model skipped the tags
            cleaned = self.clean_result(response.strip())
            return cleaned or None

        return None
