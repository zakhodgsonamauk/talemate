"""Unit tests for the director autonomy/malleability levers
(director-trust-and-levers track, Phase 3).

Covers:
- New scene_direction configs exist with correct defaults in the agent's
  actions (as produced by DirectorAgent.init_actions / add_scene_direction_actions).
- Config property helpers return the defaults.
- Frequency gate: passes every round at 1, every Nth eligible round at N,
  and direction_execute_turn early-returns ([], False) on gated rounds.
- Stale-beat pressure: counts static rounds for the active plan's current
  task, resets when the task advances, returns the escalation payload at
  the threshold, and is off at 0.
- Guide pacing: guide_* methods pass the pacing config to their templates.
"""

from __future__ import annotations

import pytest

from conftest import MockScene, bootstrap_scene

import talemate.instance as instance
from _director_test_helpers import patch_prompt_request_in
from talemate.agents.context import ActiveAgent
from talemate.agents.director.plan.schema import Plan, PlanStatus, Task
from talemate.agents.director.plan.util import save_plan


@pytest.fixture
def scene():
    s = MockScene()
    bootstrap_scene(s)
    return s


@pytest.fixture
def director(scene):
    return instance.get_agent("director")


EXPECTED_DEFAULTS = {
    "stance": "nudge",
    "adjudication": True,
    "adjudication_window": 3,
    "stale_beat_rounds": 6,
    "frequency": 1,
    "pacing": "steady",
    "player_agency": "consequences",
}


class TestLeverConfigDefaults:
    def test_configs_exist_with_defaults(self, director):
        config = director.actions["scene_direction"].config
        for key, expected in EXPECTED_DEFAULTS.items():
            assert key in config, f"scene_direction config missing {key}"
            assert config[key].value == expected, key

    def test_property_helpers(self, director):
        assert director.direction_stance == "nudge"
        assert director.direction_adjudication is True
        assert director.direction_adjudication_window == 3
        assert director.direction_stale_beat_rounds == 6
        assert director.direction_frequency == 1
        assert director.direction_pacing == "steady"
        assert director.direction_player_agency == "consequences"

    def test_stance_choices(self, director):
        config = director.actions["scene_direction"].config
        values = [c["value"] for c in config["stance"].choices]
        assert values == ["hands_off", "nudge", "drive", "showrunner"]
        agency_values = [c["value"] for c in config["player_agency"].choices]
        assert agency_values == ["strict", "consequences", "assist"]
        pacing_values = [c["value"] for c in config["pacing"].choices]
        assert pacing_values == ["simmer", "steady", "escalating"]


def _set_config(director, key, value):
    director.actions["scene_direction"].config[key].value = value


class TestFrequencyGate:
    def test_default_passes_every_round(self, director):
        assert all(director._direction_frequency_gate() for _ in range(5))

    def test_every_second_round(self, director):
        _set_config(director, "frequency", 2)
        results = [director._direction_frequency_gate() for _ in range(6)]
        assert results == [False, True, False, True, False, True]

    def test_every_third_round(self, director):
        _set_config(director, "frequency", 3)
        results = [director._direction_frequency_gate() for _ in range(6)]
        assert results == [False, False, True, False, False, True]

    @pytest.mark.asyncio
    async def test_execute_turn_gated_shape(self, director):
        """A gated round early-returns ([], False) without generating."""
        director.actions["scene_direction"].enabled = True
        _set_config(director, "frequency", 2)
        try:
            result = await director.direction_execute_turn()
        finally:
            director.actions["scene_direction"].enabled = False
        assert result == ([], False)

    @pytest.mark.asyncio
    async def test_always_on_bypasses_gate(self, director, monkeypatch):
        _set_config(director, "frequency", 99)

        async def fake_generate(**kwargs):
            return (["generated"], True)

        monkeypatch.setattr(director, "_direction_generate", fake_generate)
        result = await director.direction_execute_turn(always_on=True)
        assert result == (["generated"], True)


def _make_plan(scene, *, status=PlanStatus.executing, task_count=2) -> Plan:
    plan = Plan(
        instructions="test plan",
        status=status,
        tasks=[Task(description=f"task {i}", order=i + 1) for i in range(task_count)],
    )
    save_plan(scene, plan)
    return plan


class TestStaleBeatPressure:
    def test_off_at_zero(self, director, scene):
        _set_config(director, "stale_beat_rounds", 0)
        _make_plan(scene)
        assert director._direction_compute_stale_beat_pressure() is None

    def test_no_plan_returns_none(self, director):
        assert director._direction_compute_stale_beat_pressure() is None

    def test_escalates_at_threshold(self, director, scene):
        _set_config(director, "stale_beat_rounds", 3)
        plan = _make_plan(scene)
        assert director._direction_compute_stale_beat_pressure() is None  # round 1
        assert director._direction_compute_stale_beat_pressure() is None  # round 2
        payload = director._direction_compute_stale_beat_pressure()  # round 3
        assert payload is not None
        assert payload["rounds"] == 3
        assert plan.tasks[0].description in payload["task"]

    def test_resets_when_task_advances(self, director, scene):
        _set_config(director, "stale_beat_rounds", 2)
        plan = _make_plan(scene)
        assert director._direction_compute_stale_beat_pressure() is None  # round 1
        # task completes -> next pending task differs -> counter restarts
        plan.complete_task(plan.tasks[0].id)
        save_plan(scene, plan)
        assert director._direction_compute_stale_beat_pressure() is None  # round 1
        payload = director._direction_compute_stale_beat_pressure()  # round 2
        assert payload is not None
        assert plan.tasks[1].description in payload["task"]

    def test_prefers_executing_plan(self, director, scene):
        _set_config(director, "stale_beat_rounds", 1)
        _make_plan(scene, status=PlanStatus.ready)
        executing = _make_plan(scene, status=PlanStatus.executing)
        payload = director._direction_compute_stale_beat_pressure()
        assert payload is not None
        assert executing.tasks[0].description in payload["task"]


def _noop_fn():  # ActiveAgent requires a fn reference
    pass


class TestGuidePacingFlows:
    @pytest.mark.asyncio
    async def test_narrator_guidance_receives_pacing(
        self, director, scene, monkeypatch
    ):
        _set_config(director, "pacing", "escalating")
        install = patch_prompt_request_in(monkeypatch)
        stub = install({"director.guide-narration": [("raw", {"guidance": "x"})]})
        with ActiveAgent(director, _noop_fn):
            await director.guide_narrator_off_of_scene_analysis("analysis")
        assert stub.calls[0]["vars"]["pacing"] == "escalating"

    @pytest.mark.asyncio
    async def test_actor_guidance_receives_pacing(self, director, scene, monkeypatch):
        _set_config(director, "pacing", "simmer")
        from talemate.character import Character

        char = Character(
            name="Alice",
            description="",
            base_attributes={},
            details={},
            color="#fff",
        )
        install = patch_prompt_request_in(monkeypatch)
        stub = install({"director.guide-conversation": [("raw", {"guidance": "x"})]})
        with ActiveAgent(director, _noop_fn):
            await director.guide_actor_off_of_scene_analysis("analysis", char)
        assert stub.calls[0]["vars"]["pacing"] == "simmer"
