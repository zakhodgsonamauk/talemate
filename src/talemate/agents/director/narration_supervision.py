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
                "frequency": AgentActionConfig(
                    type="number",
                    label="Frequency",
                    description=(
                        "Supervise every Nth narration. The review always sees the "
                        "full scene history, so the narrations skipped in between are "
                        "still covered by the next review - but only the latest draft "
                        "can be corrected before it reaches the scene."
                    ),
                    value=1,
                    min=1,
                    max=10,
                    step=1,
                ),
                "precheck_client": AgentActionConfig(
                    type="text",
                    label="Pre-check client",
                    description=(
                        "Client (by name) for a cheap PASS/ISSUES triage before the "
                        "full supervision generation. The expensive full review on the "
                        "director's client only runs when triage flags issues. Leave "
                        "empty to always run the full review. Ignored in Author mode. "
                        "Pick a fast model that will not refuse your content - a "
                        "refusal is treated as ISSUES and escalates to the full review."
                    ),
                    value="",
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

    @property
    def narration_supervision_frequency(self) -> int:
        return int(self.resolve_config("supervise_narration", "frequency"))

    @property
    def narration_supervision_precheck_client(self) -> str:
        return (
            self.resolve_config("supervise_narration", "precheck_client") or ""
        ).strip()

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

        if not self._narration_supervision_frequency_gate():
            return

        # cheap triage on a separate fast client - only escalate to the
        # expensive full review when it flags issues. Author mode always
        # rewrites, so a pass/fail verdict is meaningless there.
        if self.narration_supervision_mode != "author":
            try:
                needs_full = await self.narration_supervision_precheck(draft)
            except Exception as e:
                log.error("narration_supervision.precheck_error", error=e)
                needs_full = True
            if not needs_full:
                return

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

    def _narration_supervision_frequency_gate(self) -> bool:
        """Frequency lever: pass every Nth narration.

        Counts skipped narrations in agent scene state; when the counter
        reaches N-1 skips, the next narration passes and the counter resets.
        Mirrors the scene-direction frequency gate.
        """
        frequency = self.narration_supervision_frequency
        if frequency <= 1:
            return True
        count = (
            self.get_scene_state("narration_supervision_counter", default=0) or 0
        ) + 1
        if count >= frequency:
            self.set_scene_states(narration_supervision_counter=0)
            return True
        self.set_scene_states(narration_supervision_counter=count)
        return False

    # actions

    @set_processing
    async def narration_supervision_precheck(self, draft: str) -> bool:
        """Cheap PASS/ISSUES triage on the configured pre-check client.

        Returns True when the full supervision generation should run.
        Fails open: no client configured, an unknown client name, or an
        ambiguous verdict (including a refusal) all escalate to the full
        review rather than silently skipping it.
        """
        client_name = self.narration_supervision_precheck_client
        if not client_name:
            return True

        from talemate.instance import get_client

        try:
            client = get_client(client_name)
        except KeyError:
            log.warning(
                "narration_supervision.precheck_client_not_found",
                configured=client_name,
            )
            return True

        response, _ = await Prompt.request(
            "director.supervise-narration-precheck",
            client,
            "investigate_16",
            vars={
                "scene": self.scene,
                "max_tokens": client.max_token_length,
                "draft": draft,
                "guidance": self.narration_supervision_guidance,
            },
        )

        verdict = (response or "").strip()[:40].upper()
        # check ISSUES first - if both words somehow appear, prefer the
        # full review
        if "ISSUES" in verdict:
            return True
        if "PASS" in verdict:
            return False
        return True

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
