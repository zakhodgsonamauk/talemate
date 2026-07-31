import pydantic
import structlog

from talemate.emit import emit
from talemate.instance import get_agent
from talemate.server.websocket_plugin import Plugin
from talemate.status import set_loading


from talemate.scene_message import ContextInvestigationMessage

__all__ = [
    "NarratorWebsocketHandler",
]

log = structlog.get_logger("talemate.server.narrator")


class QueryPayload(pydantic.BaseModel):
    query: str
    at_the_end: bool = True


class NarrativeDirectionPayload(pydantic.BaseModel):
    narrative_direction: str = ""


class CharacterPayload(NarrativeDirectionPayload):
    character: str = ""


class NarratorWebsocketHandler(Plugin):
    """
    Handles narrator actions
    """

    router = "narrator"

    @property
    def narrator(self):
        return get_agent("narrator")

    @set_loading("Progressing the story", cancellable=True, as_async=True)
    async def handle_progress(self, data: dict):
        """
        Progress the story (optionally to a specific direction)
        """
        payload = NarrativeDirectionPayload(**data)
        await self.narrator.action_to_narration(
            "progress_story",
            narrative_direction=payload.narrative_direction,
            emit_message=True,
        )

    @set_loading("Narrating the environment", cancellable=True, as_async=True)
    async def handle_narrate_environment(self, data: dict):
        """
        Narrate the environment (optionally to a specific direction)
        """
        payload = NarrativeDirectionPayload(**data)
        await self.narrator.action_to_narration(
            "narrate_environment",
            narrative_direction=payload.narrative_direction,
            emit_message=True,
        )

    @set_loading("Working on a query", cancellable=True, as_async=True)
    async def handle_query(self, data: dict):
        """
        Give a query or instruction to the narrator that results in a context investigation
        message.

        Story-advancing queries are escalated to the director's scene direction
        when query escalation is enabled on the narrator and scene direction is
        active on the director.
        """
        payload = QueryPayload(**data)

        if await self._escalate_query_to_director(payload.query):
            return

        narration = await self.narrator.narrate_query(**payload.model_dump())
        message: ContextInvestigationMessage = ContextInvestigationMessage(
            narration, sub_type="query"
        )
        message.set_source("narrator", "narrate_query", **payload.model_dump())

        await self.scene.push_history(message)
        emit("context_investigation", message=message)

    async def _escalate_query_to_director(self, query: str) -> bool:
        """
        Route a story-advancing query to the director's scene direction.

        Returns True when the query was escalated and handled by the director.
        """
        if not self.narrator.query_escalation_enabled:
            return False

        director = get_agent("director")
        if (
            not director
            or not director.enabled
            or not director.direction_enabled_with_override
        ):
            return False

        intent = await self.narrator.classify_query_intent(query)
        if intent != "advance":
            return False

        # deferred import to avoid a narrator <-> director import cycle
        from talemate.agents.director.scene_direction.schema import (
            UserInteractionMessage,
        )

        log.debug("narrator.query.escalated_to_director", query=query)
        emit("status", message="Query escalated to the director", status="info")

        await director.direction_append_message(
            UserInteractionMessage(user_input=query, is_direction=True)
        )
        await director.direction_execute_turn()
        return True

    @set_loading("Looking at the scene", cancellable=True, as_async=True)
    async def handle_look_at_scene(self, data: dict):
        """
        Look at the scene (optionally to a specific direction)

        This will result in a context investigation message.
        """
        payload = NarrativeDirectionPayload(**data)

        narration = await self.narrator.narrate_scene(
            narrative_direction=payload.narrative_direction
        )

        message: ContextInvestigationMessage = ContextInvestigationMessage(
            narration, sub_type="visual-scene"
        )
        message.set_source("narrator", "narrate_scene", **payload.model_dump())

        await self.scene.push_history(message)
        emit("context_investigation", message=message)

    @set_loading("Looking at a character", cancellable=True, as_async=True)
    async def handle_look_at_character(self, data: dict):
        """
        Look at a character (optionally to a specific direction)

        This will result in a context investigation message.
        """
        payload = CharacterPayload(**data)

        narration = await self.narrator.narrate_character(
            character=self.scene.get_character(payload.character),
            narrative_direction=payload.narrative_direction,
        )

        message: ContextInvestigationMessage = ContextInvestigationMessage(
            narration, sub_type="visual-character"
        )
        message.set_source("narrator", "narrate_character", **payload.model_dump())

        await self.scene.push_history(message)
        emit("context_investigation", message=message)
