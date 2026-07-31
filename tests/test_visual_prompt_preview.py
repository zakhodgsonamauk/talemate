"""
Wiring guard for the prompt-preview return leg in wsh-visualize.json.

The `visualize` websocket action is a node module, so its behaviour lives in
graph wiring rather than Python. That wiring is hand-edited JSON with UUID keys
and a separate connection map, which makes it easy to break silently — a
mis-pointed socket does not raise, it just stops firing. These tests assert the
branch structure directly against the loaded graph.

Covered:

- `return_prompt` selects the sink. Without it the composed prompt still goes
  to chat as a system message, exactly as before; with it the prompt is
  returned over the websocket instead.
- `data/Get` tolerates a missing key, which is what lets the plain Visualize
  path keep working without sending `return_prompt` at all.

Not covered (needs a live image backend): that the returned prompt actually
generates an image and attaches it to the message.
"""

import os

import pytest

import talemate.game.engine.nodes.load_definitions  # noqa: F401
import talemate.agents.visual  # noqa: F401
from talemate.game.engine.nodes.core import Graph
from talemate.game.engine.nodes.layout import load_graph_from_file
from talemate.game.engine.nodes.registry import import_talemate_node_definitions

from _node_test_helpers import run_node

MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src",
    "talemate",
    "agents",
    "visual",
    "modules",
    "wsh-visualize.json",
)


@pytest.fixture(scope="module", autouse=True)
def load_node_definitions():
    import_talemate_node_definitions()


@pytest.fixture(scope="module")
def graph() -> Graph:
    loaded, _ = load_graph_from_file(MODULE_PATH)
    return loaded


def nodes_of(graph, registry) -> list:
    collection = graph.nodes
    values = collection.values() if isinstance(collection, dict) else collection
    return [n for n in values if n.registry == registry]


def source_of(node, input_name):
    """The (node, socket_name) feeding `input_name`, or None if unconnected."""
    for socket in node.inputs:
        if socket.name == input_name:
            source = getattr(socket, "source", None)
            return (source.node, source.name) if source else None
    raise AssertionError(f"{node.title!r} has no input {input_name!r}")


class TestHandlerStillRegisters:
    """The module must keep binding to the `visualize` action on the visual
    agent — that is what the whole feature hangs off, and a bad edit can leave
    the file loadable but unregistered."""

    def test_module_declares_visualize_on_visual_agent(self, graph):
        assert graph.get_property("name") == "visualize"
        assert graph.get_property("agent") == "visual"


class TestSinkSelection:
    def test_exactly_one_websocket_response_returning_the_prompt(self, graph):
        responses = nodes_of(graph, "websocket/WebsocketResponse")
        assert len(responses) == 1
        assert responses[0].get_property("action") == "prompt_preview"

    def test_two_routers_both_gated_on_generated_prompt_only(self, graph):
        """Both branches take prompt_only from generateVisualAsset's *output*,
        not from the input flag. That output is what orders the branch after
        the prompt has actually been composed."""
        routers = nodes_of(graph, "core/ANDRouter")
        assert len(routers) == 2
        for router in routers:
            node, socket = source_of(router, "a")
            assert node.registry == "agents/visual/generateVisualAsset"
            assert socket == "prompt_only"

    def test_system_message_fires_only_when_return_prompt_is_absent(self, graph):
        emit = nodes_of(graph, "event/EmitSystemMessage")
        assert len(emit) == 1
        router, socket = source_of(emit[0], "state")
        assert router.registry == "core/ANDRouter"
        assert socket == "yes"
        # Its second flag is the inverted return_prompt, so this branch is the
        # "no return_prompt" one — i.e. today's behaviour, preserved.
        invert, _ = source_of(router, "b")
        assert invert.registry == "core/Invert"
        as_bool, _ = source_of(invert, "value")
        get, _ = source_of(as_bool, "value")
        assert get.get_property("attribute") == "return_prompt"

    def test_websocket_response_fires_only_when_return_prompt_is_set(self, graph):
        response = nodes_of(graph, "websocket/WebsocketResponse")[0]
        # FinalizePrompt sits between the router and the response — see
        # TestFinalisation for why — so walk through it to reach the gate.
        finalise, _ = source_of(response, "state")
        assert finalise.registry == "agents/visual/FinalizePrompt"
        router, socket = source_of(finalise, "state")
        assert router.registry == "core/ANDRouter"
        assert socket == "yes"
        # Straight from the flag, with no Invert in between.
        as_bool, _ = source_of(router, "b")
        assert as_bool.registry == "core/AsBool"
        get, _ = source_of(as_bool, "value")
        assert get.get_property("attribute") == "return_prompt"

    def test_the_two_branches_are_not_the_same_router(self, graph):
        emit_router, _ = source_of(nodes_of(graph, "event/EmitSystemMessage")[0], "state")
        finalise, _ = source_of(nodes_of(graph, "websocket/WebsocketResponse")[0], "state")
        ws_router, _ = source_of(finalise, "state")
        assert emit_router.id != ws_router.id


class TestPayload:
    @staticmethod
    def _collector(graph):
        collectors = nodes_of(graph, "data/DictCollector")
        assert len(collectors) == 1
        return collectors[0]

    def test_response_data_comes_from_the_collector(self, graph):
        response = nodes_of(graph, "websocket/WebsocketResponse")[0]
        collector, socket = source_of(response, "data")
        assert collector.registry == "data/DictCollector"
        assert socket == "dict"

    def test_payload_carries_the_keys_the_modal_prefills_from(self, graph):
        keys = set()
        for socket in self._collector(graph).inputs:
            source = getattr(socket, "source", None)
            if source and source.node.registry == "data/MakeKeyValuePair":
                keys.add(source.node.get_property("key"))
        assert keys == {
            "prompt",
            "negative_prompt",
            "vis_type",
            "character_name",
            "message_ids",
            # Without this the modal falls back to LANDSCAPE, which is wrong
            # for CHARACTER_CARD and OBJECT_ILLUSTRATION — the real path takes
            # PORTRAIT for both from VIS_TYPE_TO_FORMAT.
            "format",
        }

    @pytest.mark.parametrize(
        "key,expected_registry,expected_socket",
        [
            # From the FINALISED request, not from UnpackPrompt. UnpackPrompt's
            # copy is pre-finalise: an anchor for every character in frame and
            # no (trait:1.3) emphasis. See TestFinalisation.
            ("prompt", "agents/visual/FinalizePrompt", "prompt"),
            ("negative_prompt", "agents/visual/FinalizePrompt", "negative_prompt"),
            ("vis_type", "validation/ValidateValueContained", "value"),
            ("character_name", "data/Get", "value"),
            # The correlation key. If this is re-pointed the modal simply never
            # fills in, so it needs pinning as much as the prompt does.
            ("message_ids", "core/Coallesce", "value"),
            ("format", "data/Get", "value"),
        ],
    )
    def test_each_payload_value_comes_from_the_expected_source(
        self, graph, key, expected_registry, expected_socket
    ):
        pair = next(
            n
            for n in nodes_of(graph, "data/MakeKeyValuePair")
            if n.get_property("key") == key
        )
        node, socket = source_of(pair, "value")
        assert node.registry == expected_registry
        assert socket == expected_socket

    def test_format_is_read_off_the_generation_request(self, graph):
        """Rather than duplicating VIS_TYPE_TO_FORMAT in the frontend —
        generation_request.format is where SelectBackend already resolved it."""
        pair = next(
            n
            for n in nodes_of(graph, "data/MakeKeyValuePair")
            if n.get_property("key") == "format"
        )
        get, _ = source_of(pair, "value")
        assert get.get_property("attribute") == "format"
        # The finalised request, so the whole payload describes one state.
        upstream, socket = source_of(get, "object")
        assert upstream.registry == "agents/visual/FinalizePrompt"
        assert socket == "generation_request"


class TestFinalisation:
    """Prompt assembly has two halves. The graph builds the first (LLM keywords,
    art style, an anchor for every character in frame). The second lives in
    GenerationMixin._finalize_prompt and is called from exactly one place —
    visual.generate, at generation.py:815 — so a preview that skips it shows a
    materially different prompt: every character's anchor still present, and no
    emphasis at all, since _render_with_emphasis is the only producer of
    `(trait:1.3)`.

    That was the original bug: a one-character paragraph previewed as two
    characters with no weighting.
    """

    def test_the_preview_branch_finalises_the_prompt(self, graph):
        finalisers = nodes_of(graph, "agents/visual/FinalizePrompt")
        assert len(finalisers) == 1
        router, socket = source_of(finalisers[0], "state")
        assert router.registry == "core/ANDRouter"
        assert socket == "yes"
        # The return_prompt branch specifically — its flag comes straight from
        # AsBool, with no Invert.
        as_bool, _ = source_of(router, "b")
        assert as_bool.registry == "core/AsBool"

    def test_it_finalises_the_request_the_generator_built(self, graph):
        node, socket = source_of(
            nodes_of(graph, "agents/visual/FinalizePrompt")[0], "generation_request"
        )
        assert node.registry == "agents/visual/generateVisualAsset"
        assert socket == "generation_request"

    def test_the_response_runs_after_finalisation(self, graph):
        """Not off the router directly, or the payload could be queued before
        the prompt it describes has been finalised."""
        response = nodes_of(graph, "websocket/WebsocketResponse")[0]
        node, socket = source_of(response, "state")
        assert node.registry == "agents/visual/FinalizePrompt"
        assert socket == "state"

    def test_the_no_flag_fallback_is_left_pre_finalise(self, graph):
        """Deliberate: without return_prompt, behaviour must be what it was
        before this feature existed. The system message keeps reading
        UnpackPrompt, so the fallback text is unchanged."""
        body = nodes_of(graph, "data/string/AdvancedFormat")[0]
        for socket_name in ("item0", "item1"):
            node, _ = source_of(body, socket_name)
            assert node.registry == "agents/visual/UnpackPrompt"

    def test_operation_done_waits_for_the_response(self, graph):
        """The response feeds the same Coallesce the system message already
        feeds, and that Coallesce gates OperationDone — so prompt_preview is
        queued before operation_done rather than racing it."""
        response = nodes_of(graph, "websocket/WebsocketResponse")[0]
        operation_done = nodes_of(graph, "websocket/signals/OperationDone")
        assert len(operation_done) == 1
        gate, gate_socket = source_of(operation_done[0], "state")
        assert gate.registry == "core/Coallesce"
        assert gate_socket == "value"
        # ...and the response is one of that Coallesce's inputs.
        feeders = {
            s.source.node.id: s.name
            for s in gate.inputs
            if getattr(s, "source", None)
        }
        assert response.id in feeders


class TestBranchBehaviour:
    """The structural tests above pin what is wired to what. This pins what the
    wiring actually *does*, by running the real AsBool/Invert/ANDRouter chain
    over every combination of the two flags.

    The case that matters most is the first one: a normal generation must not
    emit a system message. Nothing else in the suite would catch that.
    """

    @staticmethod
    async def _run_branch(prompt_only: bool, payload: dict) -> tuple[bool, bool]:
        """Returns (system_message_fires, websocket_response_fires)."""
        from talemate.game.engine.nodes.core import Graph, GraphContext, UNRESOLVED
        from talemate.game.engine.nodes.data import Get
        from talemate.game.engine.nodes.logic import ANDRouter, AsBool, Invert

        graph = Graph(title="branch")
        get = Get()
        get.set_property("attribute", "return_prompt")
        get.set_property("object", payload)
        as_bool = AsBool(title="AB")
        as_bool.set_property("default", False)
        invert = Invert()
        # Stands in for generateVisualAsset's prompt_only output.
        prompt_only_src = AsBool(title="PO")
        prompt_only_src.set_property("value", prompt_only)
        prompt_only_src.set_property("default", False)
        to_system = ANDRouter(title="AND_SYS")
        to_websocket = ANDRouter(title="AND_WS")

        chain = (get, as_bool, invert, prompt_only_src, to_system, to_websocket)
        for node in chain:
            graph.add_node(node)
        graph.connect(get.get_output_socket("value"), as_bool.get_input_socket("value"))
        graph.connect(as_bool.get_output_socket("value"), invert.get_input_socket("value"))
        graph.connect(as_bool.get_output_socket("value"), to_websocket.get_input_socket("b"))
        graph.connect(invert.get_output_socket("value"), to_system.get_input_socket("b"))
        graph.connect(prompt_only_src.get_output_socket("value"), to_system.get_input_socket("a"))
        graph.connect(prompt_only_src.get_output_socket("value"), to_websocket.get_input_socket("a"))

        with GraphContext() as state:
            # Driven directly rather than via graph.execute(): a bare Graph has
            # no entry node, and silently running nothing would make every
            # assertion below pass for the wrong reason. The guard catches that.
            for node in chain:
                await node.run(state)
            assert prompt_only_src.get_output_socket("value").value is not UNRESOLVED, (
                "harness did not execute - results would be meaningless"
            )
            return (
                not to_system.get_output_socket("yes").deactivated,
                not to_websocket.get_output_socket("yes").deactivated,
            )

    @pytest.mark.asyncio
    async def test_normal_generation_emits_neither(self):
        """The regression that would matter most: replacing the old Switch must
        not leak a prompt dump into chat on every ordinary Visualize."""
        system, websocket = await self._run_branch(False, {})
        assert (system, websocket) == (False, False)

    @pytest.mark.asyncio
    async def test_prompt_only_without_the_flag_still_dumps_to_chat(self):
        """Today's no-backend fallback, preserved."""
        system, websocket = await self._run_branch(True, {})
        assert (system, websocket) == (True, False)

    @pytest.mark.asyncio
    async def test_prompt_only_with_the_flag_returns_over_the_websocket(self):
        system, websocket = await self._run_branch(True, {"return_prompt": True})
        assert (system, websocket) == (False, True)

    @pytest.mark.asyncio
    async def test_flag_alone_does_nothing_without_prompt_only(self):
        """return_prompt is not a way to intercept a real generation."""
        system, websocket = await self._run_branch(False, {"return_prompt": True})
        assert (system, websocket) == (False, False)


class TestFinalisationIsRepeatable:
    """This feature finalises twice: once to show the prompt, then again inside
    visual.generate when the edited prompt is submitted. That makes
    _finalize_prompt's repeatability load-bearing here in a way it is not for
    the plain path, which only ever finalises once.

    The property is already relied on by regenerate, and the individual pieces
    are covered in test_visual_anchor.py — but nothing pinned the round trip,
    which is the part that would break this feature. If emphasis ever starts
    compounding, "Adjust & Visualize" is where it shows up first.
    """

    def test_stripping_undoes_weighting(self):
        from talemate.agents.visual.style import strip_emphasis, weight_group

        keywords = ["alien woman", "violet skin", "silver markings"]
        assert strip_emphasis(weight_group(keywords, 1.3)) == ", ".join(keywords)

    def test_weighting_a_stripped_prompt_does_not_compound(self):
        from talemate.agents.visual.style import strip_emphasis, weight_group

        keywords = ["alien woman", "violet skin"]
        once = weight_group(keywords, 1.3)
        twice = weight_group(strip_emphasis(once).split(", "), 1.3)
        assert twice == once
        assert twice.count("(") == 1

    def test_a_third_pass_is_still_stable(self):
        """Two passes could coincidentally match while still drifting."""
        from talemate.agents.visual.style import strip_emphasis, weight_group

        text = weight_group(["alien woman", "violet skin"], 1.3)
        for _ in range(3):
            text = weight_group(strip_emphasis(text).split(", "), 1.3)
        assert text == "(alien woman, violet skin:1.3)"

    def test_keyword_text_survives_a_round_trip(self):
        from talemate.agents.visual.style import strip_emphasis, weight_group

        assert strip_emphasis(weight_group(["scar (old)"], 1.2)) == r"scar \(old\)"

    def test_escaped_brackets_survive_a_round_trip(self):
        """A scar "(old)" is literal content, not emphasis. weight_group used to
        escape brackets unconditionally, so a second finalise pass turned
        `\\(old\\)` into `\\\\(old\\\\)` — a literal backslash followed by an
        unescaped bracket, which opens a weight group and corrupts the parse.
        Fixed by escaping only brackets that are not already escaped."""
        from talemate.agents.visual.style import strip_emphasis, weight_group

        once = weight_group(["scar (old)"], 1.2)
        assert once == r"(scar \(old\):1.2)"
        twice = weight_group(strip_emphasis(once).split(", "), 1.2)
        assert twice == once

    def test_escapes_do_not_grow_over_repeated_passes(self):
        from talemate.agents.visual.style import strip_emphasis, weight_group

        text = weight_group(["scar (old)", "violet skin"], 1.3)
        for _ in range(4):
            text = weight_group(strip_emphasis(text).split(", "), 1.3)
        assert text.count("\\") == 2
        assert text == r"(scar \(old\), violet skin:1.3)"


class TestMissingFlagIsSafe:
    """The plain Visualize path never sends `return_prompt`. If reading an
    absent key raised, that path would break — so pin the behaviour the
    wiring relies on."""

    @pytest.mark.asyncio
    async def test_get_returns_none_for_a_key_the_payload_omits(self):
        from talemate.game.engine.nodes.data import Get

        outputs = await run_node(
            Get(), inputs={"object": {"prompt_only": True}, "attribute": "return_prompt"}
        )
        assert outputs["value"] is None

    @pytest.mark.asyncio
    async def test_as_bool_turns_that_into_false(self):
        from talemate.game.engine.nodes.logic import AsBool

        outputs = await run_node(AsBool(), inputs={"value": None, "default": False})
        assert outputs["value"] is False
