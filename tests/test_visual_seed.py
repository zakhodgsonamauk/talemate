"""Tests for image generation seed control.

Scoped honestly: in txt2img a seed applies to the whole image, not per character.
Pinning it makes palette and rendering consistent across illustrations. It does NOT
make two crew members the same people shot to shot - that is what visual anchors are
for (see tests/test_visual_anchor.py and docs/fork/visual-consistency-design.md).
"""

import pytest

from talemate.agents.visual.schema import SEED_MODE, SamplerSettings, resolve_seed
from talemate.tale_mate import Scene


def test_sampler_settings_seed_defaults_to_none():
    assert SamplerSettings().seed is None


def test_random_mode_returns_none():
    """None means "let the backend reseed", which is the behaviour before this change."""
    scene = Scene()
    assert resolve_seed(SEED_MODE.RANDOM, scene=scene, fixed_seed=1234) is None


def test_scene_mode_is_stable_for_one_scene():
    """AC8. Two generations in the same scene resolve to the same seed."""
    scene = Scene()

    first = resolve_seed(SEED_MODE.SCENE, scene=scene)
    second = resolve_seed(SEED_MODE.SCENE, scene=scene)

    assert first == second
    assert isinstance(first, int)


def test_scene_mode_differs_between_scenes():
    a, b = Scene(), Scene()

    assert resolve_seed(SEED_MODE.SCENE, scene=a) != resolve_seed(
        SEED_MODE.SCENE, scene=b
    )


def test_scene_mode_stays_inside_the_backend_range():
    """A1111 rejects a seed outside its 32-bit range."""
    for _ in range(50):
        seed = resolve_seed(SEED_MODE.SCENE, scene=Scene())
        assert 0 <= seed <= 2**32 - 1


def test_fixed_mode_uses_the_configured_value():
    scene = Scene()
    assert resolve_seed(SEED_MODE.FIXED, scene=scene, fixed_seed=987654) == 987654


def test_fixed_mode_without_a_value_falls_back_to_random():
    scene = Scene()
    assert resolve_seed(SEED_MODE.FIXED, scene=scene, fixed_seed=None) is None


def test_scene_mode_without_a_scene_falls_back_to_random():
    assert resolve_seed(SEED_MODE.SCENE, scene=None) is None


# ---------------------------------------------------------------------------
# A1111 payload
# ---------------------------------------------------------------------------


@pytest.fixture
def a1111_backend():
    from talemate.agents.visual.backends.automatic1111 import Backend

    return Backend(api_url="http://localhost:5001")


def _request(seed):
    from talemate.agents.visual.schema import GenerationRequest

    return GenerationRequest(
        prompt="starship interior, violet-skinned officer",
        sampler_settings=SamplerSettings(seed=seed),
    )


def test_a1111_payload_omits_seed_when_unset(a1111_backend):
    payload = a1111_backend.build_payload(_request(None))

    assert "seed" not in payload


def test_a1111_payload_carries_seed_when_set(a1111_backend):
    payload = a1111_backend.build_payload(_request(4242))

    assert payload["seed"] == 4242
