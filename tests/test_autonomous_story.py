"""
Tests for the autonomous-story track.

Covers the AI-turn cap becoming configuration rather than a literal welded into
`scene-loop.json`, and its exposure to the node graph.

Track: conductor/tracks/autonomous-story/
"""

import json
from pathlib import Path

import pytest

from _node_test_helpers import run_node

from talemate.config.schema import General


REPO_ROOT = Path(__file__).resolve().parents[1]
SCENE_LOOP = (
    REPO_ROOT
    / "src"
    / "talemate"
    / "game"
    / "engine"
    / "nodes"
    / "modules"
    / "scene"
    / "scene-loop.json"
)


# === T2 — max_ai_turns in game config ===


def test_general_has_max_ai_turns_default():
    """The cap is a backstop, not the primary pacing mechanism.

    T4 moved the hardcoded 3 into config without changing behaviour; T4b raised
    the default once direct-address hand-back (T8) was wired in. The director's
    own yield now decides pacing, and this only catches a runaway.
    """
    general = General()
    assert general.max_ai_turns == 12


def test_max_ai_turns_round_trips():
    general = General(max_ai_turns=12)
    assert general.max_ai_turns == 12
    assert General(**general.model_dump()).max_ai_turns == 12


def test_max_ai_turns_rejects_zero_and_negative():
    """A cap of zero or less would mean the AI never gets a turn."""
    with pytest.raises(ValueError):
        General(max_ai_turns=0)
    with pytest.raises(ValueError):
        General(max_ai_turns=-1)


# === T3 — exposure to the node graph ===


def test_get_scene_state_declares_max_ai_turns_output():
    """`GetSceneState` is how the loop reads game settings; the cap must be there."""
    from talemate.game.engine.nodes.scene import GetSceneState

    node = GetSceneState()
    node.setup()
    assert "max_ai_turns" in {socket.name for socket in node.outputs}


# === T4 — the graph reads config instead of a literal ===


def _load_scene_loop() -> tuple[dict, dict]:
    data = json.loads(SCENE_LOOP.read_text(encoding="utf-8"))
    raw_nodes = data["nodes"]
    nodes = (
        raw_nodes
        if isinstance(raw_nodes, dict)
        else {node["id"]: node for node in raw_nodes}
    )
    return data, nodes


def test_scene_loop_cap_is_not_a_hardcoded_literal():
    """The `MAX AI TURNS - 3` Make node must no longer drive the comparison.

    Guards the regression this whole track exists to fix: a literal 3 feeding
    `Compare.b` dragged the player back in every third beat regardless of what the
    director wanted.
    """
    data, nodes = _load_scene_loop()

    literal_ids = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "data/number/Make"
        and "MAX AI TURNS" in (node.get("title") or "")
    }

    feeding_compare = [
        (source, destination)
        for source, destinations in data["edges"].items()
        if source.rpartition(".")[0] in literal_ids
        for destination in destinations
        if nodes.get(destination.rpartition(".")[0], {}).get("registry")
        == "data/number/Compare"
    ]

    assert not feeding_compare, (
        "a hardcoded MAX AI TURNS literal still feeds the turn-cap comparison: "
        f"{feeding_compare}"
    )


def test_scene_loop_cap_reads_max_ai_turns_from_scene_state():
    """`GetSceneState.max_ai_turns` must reach a Compare node."""
    data, nodes = _load_scene_loop()

    sources = [
        source
        for source in data["edges"]
        if nodes.get(source.rpartition(".")[0], {}).get("registry")
        == "scene/GetSceneState"
        and source.rpartition(".")[2] == "max_ai_turns"
    ]

    assert sources, "no GetSceneState.max_ai_turns output is wired into the loop"

    reaches_compare = any(
        nodes.get(destination.rpartition(".")[0], {}).get("registry")
        == "data/number/Compare"
        for source in sources
        for destination in data["edges"][source]
    )

    assert reaches_compare, (
        "GetSceneState.max_ai_turns is wired, but not into the turn-cap comparison"
    )


# === T7 — direct-address detection ===
#
# Deliberately biased toward false negatives. A miss is absorbed by the
# max_ai_turns backstop and reads as the story being slow to notice you; a false
# positive hands control back constantly and recreates the original complaint.


def _addressed(body: str, player: str = "Rennick", others=("Kaeda", "Vex")) -> bool:
    from talemate.scene.address import addresses_player
    from talemate.scene_message import CharacterMessage

    return addresses_player(
        CharacterMessage(message=body), player_name=player, other_names=list(others)
    )


@pytest.mark.parametrize(
    "body",
    [
        # vocative plus second person
        'Kaeda: "Rennick, what do you make of it?"',
        # second person alone, player is the only other party present
        'Kaeda: "Do you actually believe that?"',
        # vocative at the end of the line
        'Kaeda: "That was reckless, Rennick."',
        # acted upon inside an action segment
        "Kaeda: *shoves Rennick against the bulkhead* \"Enough.\"",
        # second person in an action segment
        "Kaeda: *presses the datapad into your hand*",
    ],
)
def test_direct_address_positive(body):
    assert _addressed(body) is True


@pytest.mark.parametrize(
    "body",
    [
        # third-person talk about the player — the key negative
        'Kaeda: "Rennick has been quiet since the jump."',
        # third person, no second-person pronoun anywhere
        'Kaeda: "He never did trust the captain."',
        # another character is the one being addressed
        'Kaeda: "Vex, can you run the diagnostic?"',
        # pure scene-setting, nobody addressed
        "Kaeda: *stares out at the drifting hull*",
        # player named in narration only, as subject of someone else's report
        'Kaeda: "The log says Rennick signed off on it."',
    ],
)
def test_direct_address_negative(body):
    assert _addressed(body) is False


def test_direct_address_ignores_the_players_own_message():
    """The player's own line must never count as addressing the player."""
    from talemate.scene.address import addresses_player
    from talemate.scene_message import CharacterMessage

    message = CharacterMessage(message='Rennick: "What do you want from me?"')
    assert (
        addresses_player(
            message, player_name="Rennick", other_names=["Kaeda", "Vex"]
        )
        is False
    )


def test_direct_address_is_case_insensitive_on_names():
    assert _addressed('Kaeda: "rennick, are you listening?"') is True


def test_direct_address_handles_message_without_name_prefix():
    """Must not raise on a malformed message lacking the "Name: " prefix."""
    from talemate.scene.address import addresses_player
    from talemate.scene_message import CharacterMessage

    assert (
        addresses_player(
            CharacterMessage(message="no colon here at all"),
            player_name="Rennick",
            other_names=[],
        )
        is False
    )


def test_direct_address_second_person_ambiguous_with_another_named_party():
    """Second person plus another character named vocatively is not the player.

    Conservative by design: when the addressee is ambiguous, do not interrupt.
    """
    assert _addressed('Kaeda: "Vex, you take the helm."') is False


# === T9a — dice confirmation gate ===


class _FakeCharacter:
    def __init__(self, name):
        self.name = name


class _FakeScene:
    def __init__(self, player_name):
        self._player = _FakeCharacter(player_name) if player_name else None

    def get_player_character(self):
        return self._player


@pytest.mark.parametrize(
    "name,player,expected",
    [
        ("Rennick", "Rennick", True),
        ("rennick", "Rennick", True),  # case-insensitive
        ("  Rennick  ", "Rennick", True),  # whitespace tolerant
        ("Kaeda", "Rennick", False),
        ("", "Rennick", False),  # blank means "not tied to a character"
        (None, "Rennick", False),  # argument omitted entirely
        ("Rennick", None, False),  # no player character in the scene
    ],
)
@pytest.mark.asyncio
async def test_is_player_character_name(name, player, expected):
    """Blank or unknown resolves to not-the-player, preserving current behaviour."""
    from talemate.game.engine.nodes.scene import IsPlayerCharacterName

    node = IsPlayerCharacterName()
    out = await run_node(
        node, scene=_FakeScene(player), inputs={"name": name}
    )
    assert out["is_player"] is expected


GAMEPLAY_MODULE = (
    REPO_ROOT
    / "src"
    / "talemate"
    / "agents"
    / "director"
    / "modules"
    / "director-action-gameplay.json"
)


def _load_gameplay() -> tuple[dict, dict]:
    data = json.loads(GAMEPLAY_MODULE.read_text(encoding="utf-8"))
    raw = data["nodes"]
    nodes = raw if isinstance(raw, dict) else {n["id"]: n for n in raw}
    return data, nodes


def _feeders(data, nodes, target_id, socket):
    return [
        nodes.get(source.rpartition(".")[0], {})
        for source, destinations in data["edges"].items()
        if f"{target_id}.{socket}" in destinations
    ]


def _roll_node_id(nodes):
    return next(
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "agents/director/gameplay/rollDice"
    )


def test_roll_dice_takes_a_character_argument():
    """Without this the graph cannot know whose action a roll resolves.

    Whose roll it is previously existed only in the free-text `reason`.
    """
    _, nodes = _load_gameplay()
    arguments = {
        node["properties"].get("name")
        for node in nodes.values()
        if node.get("registry") == "focal/Argument"
    }
    assert "character" in arguments


def test_roll_is_gated_not_unconditional():
    """`rollDice.state` is required, so gating it prevents the roll entirely."""
    data, nodes = _load_gameplay()
    roll = _roll_node_id(nodes)

    feeders = _feeders(data, nodes, roll, "state")
    assert feeders, "rollDice.state has no feeder — the roll can never fire"
    assert all(
        feeder.get("registry") != "core/MakeBool" for feeder in feeders
    ), "rollDice.state is still fed by an unconditional MakeBool"
    assert any(
        feeder.get("registry") == "core/Coallesce" for feeder in feeders
    ), "rollDice.state should be gated by the confirm/not-player merge"


def test_non_player_rolls_still_resolve_without_asking():
    """The not-player branch must reach the gate, or every roll would need a prompt."""
    data, nodes = _load_gameplay()

    checks = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "scene/IsPlayerCharacterName"
    }
    assert checks, "no IsPlayerCharacterName node in the gameplay module"

    gates = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "core/Coallesce"
    }

    no_branch_reaches_gate = any(
        source.rpartition(".")[0] in checks
        and source.rpartition(".")[2] == "no"
        and any(d.rpartition(".")[0] in gates for d in destinations)
        for source, destinations in data["edges"].items()
    )
    assert no_branch_reaches_gate, (
        "the 'not the player' branch does not reach the roll gate — rolls for other "
        "characters would stop and ask"
    )


def test_confirmation_uses_an_awaitable_choice_element():
    """A notice element would not block; only choice elements are awaitable."""
    data, nodes = _load_gameplay()

    elements = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "ux/BuildChoiceElement"
    }
    assert elements, "no choice element — nothing would wait for the player"

    emits = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "ux/EmitElement"
    }
    element_feeds_emit = any(
        source.rpartition(".")[0] in elements
        and any(d.rpartition(".")[0] in emits for d in destinations)
        for source, destinations in data["edges"].items()
    )
    assert element_feeds_emit, "the choice element is never emitted"


def test_player_rolls_are_always_shown_to_the_player():
    """Being asked and then not shown the result would be worse than not asking."""
    data, nodes = _load_gameplay()
    roll = _roll_node_id(nodes)

    feeders = _feeders(data, nodes, roll, "emit_system_message")
    assert feeders, "emit_system_message has no feeder"
    assert all(
        feeder.get("registry") == "core/AsBool" for feeder in feeders
    ), "emit_system_message should come through an AsBool guard"
    for feeder in feeders:
        assert feeder["properties"].get("default") is False


# === T19 — abstraction bridge ===

ABSTRACT_TEMPLATE = (
    REPO_ROOT
    / "src"
    / "talemate"
    / "prompts"
    / "templates"
    / "director"
    / "scene-direction-abstract-context.jinja2"
)


def test_abstract_context_defaults_off():
    """Existing behaviour must be unchanged until deliberately switched on."""
    from talemate.agents.director.scene_direction.mixin import SceneDirectionMixin

    actions = {}
    SceneDirectionMixin.add_scene_direction_actions(actions)
    assert (
        actions["scene_direction"].config["abstract_context"].value is False
    )


def test_abstract_template_never_reaches_for_verbatim_dialogue():
    """Structural guarantee, not a behavioural hope.

    `scene.context_history` mixes summaries with verbatim dialogue by design, so
    the abstracted template must not call it or include the template that does.
    """
    source = ABSTRACT_TEMPLATE.read_text(encoding="utf-8")
    assert "context_history" not in source
    assert "scene-context-chat" not in source
    assert "scene.history" not in source


def test_abstract_template_is_selected_by_config():
    scene_direction = (
        REPO_ROOT
        / "src"
        / "talemate"
        / "prompts"
        / "templates"
        / "director"
        / "scene-direction.jinja2"
    ).read_text(encoding="utf-8")

    assert "director.scene_direction.abstract_context" in scene_direction
    assert "scene-direction-abstract-context.jinja2" in scene_direction
    # the verbatim path must still exist for the default case
    assert "scene-context-chat.jinja2" in scene_direction


def test_player_was_addressed_node_is_registered_with_expected_sockets():
    from talemate.game.engine.nodes.scene import PlayerWasAddressed

    node = PlayerWasAddressed()
    node.setup()
    outputs = {socket.name for socket in node.outputs}
    assert {"addressed", "yes", "no", "state"} <= outputs


# === T8 — direct address wired into the hand-back path ===


def test_direct_address_is_wired_into_the_loop():
    """The detector must reach the flag that gates `Select Actor For Turn`."""
    data, nodes = _load_scene_loop()

    detectors = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "scene/PlayerWasAddressed"
    }
    assert detectors, "no PlayerWasAddressed node in the scene loop"

    outgoing = [
        destination
        for source, destinations in data["edges"].items()
        if source.rpartition(".")[0] in detectors
        for destination in destinations
    ]
    assert outgoing, "PlayerWasAddressed is present but connected to nothing"


def test_hand_back_merges_yield_and_address_through_one_writer():
    """Both signals must feed a single writer, not race as two writers.

    Stage 0 writes the cap result and stage 2 overwrites it, so a second
    independent writer to `shared.skip_to_player` would be order-dependent. The
    OR is merged upstream instead, leaving exactly one conditional writer.
    """
    data, nodes = _load_scene_loop()

    writers = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry")
        in ("state/SetState", "state/ConditionalSetState")
        and node.get("properties", {}).get("name") == "skip_to_player"
    }
    conditional = {
        node_id
        for node_id in writers
        if nodes[node_id]["registry"] == "state/ConditionalSetState"
    }
    assert len(conditional) == 1, (
        "expected exactly one ConditionalSetState writing skip_to_player; "
        f"found {len(conditional)} — a second writer reintroduces the ordering hazard"
    )


def test_hand_back_signal_cannot_be_unresolved():
    """An `AsBool` must sit between the OR router and the writer.

    `ORRouter` deactivates its inactive output, and `ConditionalSetState` writes
    whatever value it receives, so without the cast the flag could be written as
    UNRESOLVED rather than False.
    """
    data, nodes = _load_scene_loop()

    routers = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "core/ORRouter"
        and "addressed" in (node.get("title") or "").lower()
    }
    assert routers, "no OR router merging the hand-back signals"

    casts = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "core/AsBool"
    }
    assert casts, "no AsBool guarding the hand-back signal"

    router_feeds_cast = any(
        destination.rpartition(".")[0] in casts
        for source, destinations in data["edges"].items()
        if source.rpartition(".")[0] in routers
        for destination in destinations
    )
    assert router_feeds_cast, "the OR router does not feed the AsBool cast"

    for node_id in casts:
        assert nodes[node_id]["properties"].get("default") is False, (
            "AsBool must default to False, or an unresolved signal would force a "
            "hand-back on every turn"
        )


def test_scene_direction_yield_no_longer_writes_the_flag_directly():
    """The director's yield must now pass through the merge, not bypass it."""
    data, nodes = _load_scene_loop()

    scene_direction = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "agents/director/SceneDirection"
    }
    routers = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "core/ORRouter"
    }

    for source, destinations in data["edges"].items():
        if (
            source.rpartition(".")[0] in scene_direction
            and source.rpartition(".")[2] == "yield_to_user"
        ):
            targets = {destination.rpartition(".")[0] for destination in destinations}
            assert targets & routers, (
                "SceneDirection.yield_to_user must feed the OR router so the "
                "address signal cannot be overwritten"
            )


def test_scene_loop_still_parses_and_keeps_the_yield_path():
    """The director's yield must survive the rewire.

    The cap and the director's yield are independent writers to
    `shared.skip_to_player`; changing one must not disturb the other.
    """
    data, nodes = _load_scene_loop()

    conditional_setters = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "state/ConditionalSetState"
        and node.get("properties", {}).get("name") == "skip_to_player"
    }
    assert conditional_setters, (
        "the ConditionalSetState writing shared.skip_to_player is gone — "
        "the director's yield_to_user path has been broken"
    )

    scene_direction_ids = {
        node_id
        for node_id, node in nodes.items()
        if node.get("registry") == "agents/director/SceneDirection"
    }
    assert scene_direction_ids, "the SceneDirection node is gone from the scene loop"

    yields = [
        source
        for source in data["edges"]
        if source.rpartition(".")[0] in scene_direction_ids
        and source.rpartition(".")[2] == "yield_to_user"
    ]
    assert yields, "SceneDirection.yield_to_user is no longer connected to anything"
