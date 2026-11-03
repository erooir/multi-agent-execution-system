"""Native Agno Toolkit and Skills construction with read-only governance."""

from __future__ import annotations

from pathlib import Path

from agno.skills import LocalSkills, Skills
from agno.tools.calculator import CalculatorTools
from agno.tools.duckduckgo import DuckDuckGoTools


class ReadOnlySkills(Skills):
    def get_tools(self):
        if not hasattr(self, "_read_only_tools"):
            self._read_only_tools = [tool for tool in super().get_tools() if tool.name != "get_skill_script"]
            for tool in self._read_only_tools:
                properties = {"skill_name": {"type": "string"}}
                required = ["skill_name"]
                if tool.name == "get_skill_reference":
                    properties["reference_path"] = {"type": "string"}
                    required.append("reference_path")
                tool.parameters = {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
        return self._read_only_tools


def create_native_toolkit(provider, include_tools=None):
    if provider == "calculator":
        return CalculatorTools(include_tools=include_tools)
    if provider == "duckduckgo":
        return DuckDuckGoTools(
            enable_news=False,
            fixed_max_results=5,
            timeout=10,
            include_tools=include_tools,
        )
    raise ValueError("native_toolkit_not_allowed")


def load_native_skills(specs, directory):
    root = Path(directory)
    for spec in specs:
        target = root / (spec["name"] if spec.get("external") else spec["id"])
        target.mkdir(parents=True, exist_ok=True)
        for name, content in spec["files"].items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return ReadOnlySkills([LocalSkills(str(root), validate=True)])
