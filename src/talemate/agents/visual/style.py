import structlog

from .anchors import characters_in_frame
from .schema import VIS_TYPE, VisualPrompt, VisualPromptPart

from talemate.agents.base import AgentAction, AgentActionConfig, AgentActionNote
from talemate.world_state.manager import WorldStateManager
from talemate.world_state.templates import Collection
from talemate.world_state.templates.visual import VisualStyle

__all__ = [
    "StyleMixin",
]

log = structlog.get_logger("talemate.agents.visual.style")

# Vis types depicting a thing rather than a cast. Character anchors are meaningless
# here and actively harmful - anchoring the crew into an object study would put people
# in a picture of a sidearm.
VIS_TYPES_WITHOUT_CAST = {
    VIS_TYPE.OBJECT_ILLUSTRATION,
    VIS_TYPE.SCENE_BACKGROUND,
}


def split_anchor(anchor: str) -> list[str]:
    """Comma-delimited anchor string to keyword list, blanks dropped."""
    return [token.strip() for token in anchor.split(",") if token.strip()]


class StyleMixin:
    @classmethod
    def add_actions(cls, actions: dict[str, AgentAction]):
        actions["_styles"] = AgentAction(
            enabled=True,
            container=True,
            label="Styles",
            icon="mdi-palette",
            description="Style configuration",
            config={
                "art_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__digital_art",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "STYLE"},
                    label="Art Style",
                    description="The default art style to use for visual prompt generation. Can be overridden in scene settings.",
                    choices=[],
                ),
                "character_card_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__character_card",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "CHARACTER_CARD"},
                    label="Character Card",
                    description="The style to use for character card visual prompt generation",
                    choices=[],
                ),
                "character_portrait_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__character_portrait",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "CHARACTER_PORTRAIT"},
                    label="Character Portrait",
                    description="The style to use for character portrait visual prompt generation",
                    choices=[],
                ),
                "scene_card_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__scene_card",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "SCENE_CARD"},
                    label="Scene Card",
                    description="The style to use for scene card visual prompt generation",
                    choices=[],
                ),
                "scene_background_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__scene_background",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "SCENE_BACKGROUND"},
                    label="Scene Background",
                    description="The style to use for scene background visual prompt generation",
                    choices=[],
                ),
                "scene_illustration_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__scene_illustration",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "SCENE_ILLUSTRATION"},
                    label="Scene Illustration",
                    description="The style to use for scene illustration visual prompt generation",
                    choices=[],
                ),
                "object_illustration_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__object_illustration",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "OBJECT_ILLUSTRATION"},
                    label="Object Illustration",
                    description="The style to use for object illustration visual prompt generation",
                    choices=[],
                    note=AgentActionNote(
                        title="Manage styles",
                        text="Additional styles can be created in the Templates manager.",
                        icon="mdi-cube-scan",
                    ),
                ),
            },
        )
        return actions

    # helpers

    def style_template_id(self, vis_type: VIS_TYPE) -> str:
        if vis_type == VIS_TYPE.UNSPECIFIED:
            return self.resolve_config("_styles", "art_style")
        return self.resolve_config("_styles", f"{vis_type.value.lower()}_style")

    def style_template(self, vis_type: VIS_TYPE) -> VisualStyle | None:
        scene = getattr(self, "scene", None)
        if not scene:
            return None

        manager: WorldStateManager = scene.world_state_manager
        templates: Collection = manager.template_collection

        # Check for scene-level override for art style (UNSPECIFIED) only
        template_id = None
        if vis_type == VIS_TYPE.UNSPECIFIED and scene.visual_style_template:
            template_id = scene.visual_style_template
        else:
            template_id = self.style_template_id(vis_type)

        if not template_id:
            return None

        try:
            group_uid, template_uid = template_id.split("__")
        except ValueError:
            return None
        return templates.find_template(group_uid, template_uid)

    def _get_current_art_style_name(self) -> str | None:
        """Get the name of the currently active art style template"""
        if not getattr(self, "scene", None):
            return None
        template = self.style_template(VIS_TYPE.UNSPECIFIED)
        if template:
            return template.name
        return None

    def _get_current_art_style_source(self) -> str | None:
        """Get the source of the currently active art style: 'scene' or 'agent'"""
        scene = getattr(self, "scene", None)
        if not scene:
            return None
        if scene.visual_style_template:
            return "scene"
        return "agent"

    # actions

    def apply_style(
        self, prompt: VisualPrompt, template_id: str
    ) -> VisualPromptPart | None:
        template = self.style_template(template_id)
        part = None
        if template:
            part = VisualPromptPart(**template.model_dump())
            prompt.parts.insert(0, part)
        return part

    async def apply_styles(
        self, prompt: VisualPrompt, vis_type: VIS_TYPE
    ) -> VisualPrompt:
        """
        Assemble the final prompt: styles, then anchors, then whatever the LLM said.

        Async because anchor derivation may need one LLM call the first time a subject
        is seen. Every call after that is cached and does no I/O.
        """
        # Snapshot before we insert anything. These are the LLM's parts, and knowing
        # exactly which they are is what lets the sanitiser (T7) touch only them.
        llm_parts = list(prompt.parts)

        await self._insert_anchors(prompt, vis_type, llm_parts)

        template_art_style: VisualStyle | None = self.style_template(
            VIS_TYPE.UNSPECIFIED
        )
        template_subject_style: VisualStyle | None = self.style_template(vis_type)

        log.debug(
            "apply_styles",
            template_art_style=template_art_style,
            template_subject_style=template_subject_style,
        )

        if template_subject_style:
            prompt.parts.insert(
                0,
                VisualPromptPart(
                    positive_keywords_raw=template_subject_style.positive_keywords,
                    negative_keywords_raw=template_subject_style.negative_keywords,
                    positive_descriptive=template_subject_style.positive_descriptive,
                    negative_descriptive=template_subject_style.negative_descriptive,
                    instructions=template_subject_style.instructions,
                ),
            )

        if template_art_style:
            prompt.parts.insert(
                0,
                VisualPromptPart(
                    positive_keywords_raw=template_art_style.positive_keywords,
                    negative_keywords_raw=template_art_style.negative_keywords,
                    positive_descriptive=template_art_style.positive_descriptive,
                    negative_descriptive=template_art_style.negative_descriptive,
                    instructions=template_art_style.instructions,
                ),
            )

        return prompt

    async def _insert_anchors(
        self,
        prompt: VisualPrompt,
        vis_type: VIS_TYPE,
        llm_parts: list[VisualPromptPart],
    ) -> None:
        """
        Insert the scene anchor, then one anchor per in-frame character.

        Inserted ahead of the LLM's parts on purpose. `VisualPrompt._build_prompt`
        dedupes with `dict.fromkeys`, which keeps the first occurrence, so position is
        what makes the anchor's wording win over a vaguer duplicate from the LLM.

        Character anchors are skipped for vis types that have no cast - an object study
        should not carry the crew's appearance.
        """
        scene = getattr(self, "scene", None)
        if not scene:
            return

        # Index 0 for now; the style parts are inserted at 0 afterwards and push these
        # down, which lands everything in the intended order.
        anchor_parts: list[VisualPromptPart] = []

        scene_anchor = await self.scene_anchor(scene)
        if scene_anchor:
            anchor_parts.append(
                VisualPromptPart(positive_keywords_raw=split_anchor(scene_anchor))
            )

        if vis_type not in VIS_TYPES_WITHOUT_CAST:
            characters = list(getattr(self, "characters", None) or scene.characters)
            for character in characters_in_frame(prompt, characters):
                keywords = []

                character_anchor = await self.character_anchor(character)
                if character_anchor:
                    keywords.extend(split_anchor(character_anchor))

                # RC4: a rule labelled HARD was being handed to the prompt-writing LLM
                # and silently dropped in the keyword compression. Emit it directly.
                if character.visual_rules:
                    keywords.extend(split_anchor(character.visual_rules))

                if keywords:
                    anchor_parts.append(
                        VisualPromptPart(positive_keywords_raw=keywords)
                    )

        if not anchor_parts:
            return

        insert_at = prompt.parts.index(llm_parts[0]) if llm_parts else len(prompt.parts)
        for offset, part in enumerate(anchor_parts):
            prompt.parts.insert(insert_at + offset, part)
