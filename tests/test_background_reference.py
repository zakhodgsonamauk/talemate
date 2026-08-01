"""Background (style-transfer) reference tests - comfyui-workflow-quality."""

import json

from talemate.agents.visual.backends.comfyui import Workflow


def load_workflow(name="sdxl-ipadapter-character.json"):
    raw = json.load(open(f"templates/comfyui-workflows/{name}", encoding="utf-8"))
    return Workflow(nodes=raw.get("prompt", raw), mtime=0.0, path="test")


class TestWorkflowChain:
    def test_chain_present_and_inert_by_default(self):
        for name in (
            "sdxl-ipadapter-character.json",
            "sdxl-ipadapter-character-multi.json",
        ):
            workflow = load_workflow(name)
            titles = {
                n.get("_meta", {}).get("title") for n in workflow.nodes.values()
            }
            assert "Talemate Background Reference" in titles, name
            assert "Apply Background Reference" in titles, name
            # shipped topology: sampler reads the character chain, NOT the
            # background chain - absent a background image nothing changes
            sampler = workflow.nodes["10"]
            assert sampler["inputs"]["model"] == ["23", 0], name

    def test_background_apply_uses_style_transfer_non_face_model(self):
        workflow = load_workflow()
        apply = workflow.nodes["93"]
        assert apply["inputs"]["weight_type"] == "style transfer"
        loader = workflow.nodes[str(apply["inputs"]["ipadapter"][0])]
        assert "plus_sdxl" in loader["inputs"]["ipadapter_file"]
        assert "face" not in loader["inputs"]["ipadapter_file"]


class TestSetBackgroundReference:
    def test_rewires_sampler_when_image_provided(self):
        workflow = load_workflow()
        workflow.set_background_reference("talemate/bg_test.png")
        assert workflow.nodes["91"]["inputs"]["image"] == "talemate/bg_test.png"
        assert workflow.nodes["10"]["inputs"]["model"] == ["93", 0]

    def test_none_leaves_topology_untouched(self):
        workflow = load_workflow()
        before = json.dumps(workflow.nodes, sort_keys=True)
        workflow.set_background_reference(None)
        assert json.dumps(workflow.nodes, sort_keys=True) == before

    def test_workflow_without_chain_is_unaffected(self):
        workflow = load_workflow("default-sdxl.json")
        before = json.dumps(workflow.nodes, sort_keys=True)
        workflow.set_background_reference("talemate/bg_test.png")
        assert json.dumps(workflow.nodes, sort_keys=True) == before
