from pathlib import Path

from ruiclaw.agent.tools.apply_patch import ApplyPatchTool
from ruiclaw.agent.tools.filesystem import ReadFileTool, WriteFileTool
from ruiclaw.agent.tools.message import MessageTool


def test_builtin_tools_expose_conservative_effect_types(tmp_path: Path) -> None:
    assert ReadFileTool(workspace=tmp_path).effect_type == "read_only"
    assert WriteFileTool(workspace=tmp_path).effect_type == "workspace_write"
    assert ApplyPatchTool(workspace=tmp_path).effect_type == "workspace_write"
    assert MessageTool(workspace=tmp_path).effect_type == "external_side_effect"
