"""
Tests for the per-request checkpoint override and its sampler profiles.

Why this exists: the ComfyUI workflow's baked-in steps/cfg are tuned for the
checkpoint the workflow shipped with. Letting the Adjust & Visualize modal swap the
checkpoint without swapping those settings produces garbage in both directions - a
Lightning-distilled model at cfg 8 fries, a base SDXL model at 6 steps is mud. The
profile travels with the choice.
"""

from talemate.agents.visual.backends.comfyui import (
    MODEL_PROFILES,
    Workflow,
    model_profile,
    resolve_checkpoint,
)


def _workflow(extra_nodes: dict | None = None) -> Workflow:
    nodes = {
        "1": {
            "_meta": {"title": "Talemate Load Checkpoint"},
            "inputs": {"ckpt_name": "CyberRealisticPony_V9.safetensors"},
        },
        "2": {
            "_meta": {"title": "KSampler"},
            "inputs": {
                "steps": 30,
                "cfg": 8.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "seed": 1,
            },
        },
        "3": {
            "_meta": {"title": "Talemate Positive Prompt"},
            "inputs": {"text": ""},
        },
    }
    nodes.update(extra_nodes or {})
    return Workflow(nodes=nodes, mtime=0.0, path="test.json")


# === model_profile ===


def test_lightning_beats_the_broader_juggernaut_pattern():
    """Both patterns match the Lightning filename; order decides, and Lightning's
    distilled settings are the ones that must win."""
    profile = model_profile("Juggernaut-XI-byRunDiffusion-Lightning.safetensors")
    assert profile["steps"] == 6
    assert profile["cfg"] == 1.5


def test_base_juggernaut_gets_full_sampling():
    profile = model_profile("Juggernaut-XI-byRunDiffusion.safetensors")
    assert profile["steps"] == 35


def test_unrecognised_model_gets_no_profile():
    """No profile means the workflow's own settings stand - a wrong guess would be
    worse than leaving a tuned workflow alone."""
    assert model_profile("SomeBespokeModel_v3.safetensors") is None
    assert model_profile(None) is None
    assert model_profile("") is None


def test_lightning_pattern_is_ordered_before_juggernaut():
    """Pins the ordering the first test depends on, so a reshuffle fails loudly."""
    patterns = [pattern.pattern for pattern, _ in MODEL_PROFILES]
    assert patterns.index("lightning") < patterns.index("juggernaut")


# === Workflow.set_sampler ===


def test_set_sampler_patches_sampler_nodes():
    workflow = _workflow()
    workflow.set_sampler(model_profile("Juggernaut-XI-byRunDiffusion-Lightning.safetensors"))

    sampler = workflow.nodes["2"]["inputs"]
    assert sampler["steps"] == 6
    assert sampler["cfg"] == 1.5
    assert sampler["sampler_name"] == "dpmpp_sde"
    assert sampler["scheduler"] == "karras"


def test_set_sampler_leaves_unrelated_nodes_alone():
    workflow = _workflow()
    workflow.set_sampler({"steps": 6, "cfg": 1.5})

    assert workflow.nodes["1"]["inputs"] == {
        "ckpt_name": "CyberRealisticPony_V9.safetensors"
    }
    assert workflow.nodes["3"]["inputs"] == {"text": ""}


def test_set_sampler_reaches_every_sampler_node():
    """Two-pass workflows (base + refiner, or hires fix) carry two samplers; a
    profile applied to only one leaves the other frying the image."""
    workflow = _workflow(
        {
            "9": {
                "_meta": {"title": "KSamplerAdvanced (refiner)"},
                "inputs": {"steps": 30, "cfg": 8.0, "noise_seed": 5},
            }
        }
    )
    workflow.set_sampler({"steps": 6, "cfg": 1.5})

    assert workflow.nodes["2"]["inputs"]["cfg"] == 1.5
    assert workflow.nodes["9"]["inputs"]["cfg"] == 1.5
    assert workflow.nodes["9"]["inputs"]["steps"] == 6


def test_set_sampler_does_not_touch_seeds():
    workflow = _workflow()
    workflow.set_sampler(model_profile("Juggernaut-XI-byRunDiffusion.safetensors"))

    assert workflow.nodes["2"]["inputs"]["seed"] == 1


# === resolve_checkpoint - the precedence every generation path runs through ===


def test_request_override_beats_the_agent_model():
    model, profile = resolve_checkpoint(
        _workflow(),
        agent_model="Juggernaut-XI-byRunDiffusion.safetensors",
        override="Juggernaut-XI-byRunDiffusion-Lightning.safetensors",
    )
    assert model == "Juggernaut-XI-byRunDiffusion-Lightning.safetensors"
    assert profile["steps"] == 6


def test_agent_model_beats_the_workflow_default():
    """The path the plain Visualize chip, character cards and automatic
    generations take: no request override, only the agent's configured model."""
    model, profile = resolve_checkpoint(
        _workflow(),
        agent_model="Juggernaut-XI-byRunDiffusion.safetensors",
        override=None,
    )
    assert model == "Juggernaut-XI-byRunDiffusion.safetensors"
    assert profile["steps"] == 35


def test_workflow_default_keeps_its_own_hand_tuned_settings():
    """No choice made anywhere: the workflow JSON is authoritative, even though
    its checkpoint name would match a profile."""
    model, profile = resolve_checkpoint(_workflow(), agent_model="", override=None)
    assert model is None
    assert profile is None


def test_choosing_the_workflows_own_checkpoint_changes_nothing():
    """Selecting the model the workflow already runs must not replace its baked
    settings with the profile table's generic ones."""
    model, profile = resolve_checkpoint(
        _workflow(),
        agent_model="CyberRealisticPony_V9.safetensors",
        override=None,
    )
    assert model == "CyberRealisticPony_V9.safetensors"
    assert profile is None


def test_swapping_to_an_unrecognised_model_gets_no_profile():
    model, profile = resolve_checkpoint(
        _workflow(), agent_model="SomeBespokeModel_v3.safetensors", override=None
    )
    assert model == "SomeBespokeModel_v3.safetensors"
    assert profile is None
