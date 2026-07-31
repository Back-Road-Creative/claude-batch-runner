"""Drift guard for the example health-check agent definitions.

Each agent definition advertises its output shape with a fenced ```json block.
health-check.py validates real agent output against CATEGORY_SCHEMA /
STRATEGY_SCHEMA. If the two drift apart, the shipped examples teach a shape the
orchestrator rejects — so this test holds the documented example to the schema
the code enforces. Nothing here invokes an agent.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "health-check"
SCRIPT_PATH = EXAMPLE / "health-check.py"
AGENTS_DIR = EXAMPLE / "agents"

CATEGORY_AGENTS = [
    "health-gaps",
    "health-flaws",
    "health-completeness",
    "health-complexity",
    "health-security",
    "health-quality",
    "health-production",
]


def _load_hc():
    spec = importlib.util.spec_from_file_location("health_check", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _extract_example_json(text: str) -> dict:
    match = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
    assert match is not None, "no ```json fenced block found"
    return json.loads(match.group(1))


def test_every_category_in_the_menu_has_an_agent_definition():
    """A category the controller can select but cannot dispatch is a dead menu entry."""
    assert sorted(CATEGORY_AGENTS) == sorted(f"health-{c}" for c in _load_hc().CATEGORY_KEYS)


@pytest.mark.parametrize("agent", CATEGORY_AGENTS)
def test_category_agent_example_conforms_to_category_schema(agent):
    path = AGENTS_DIR / f"{agent}.md"
    assert path.exists(), f"{path} missing"
    text = path.read_text()
    assert "CATEGORY_SCHEMA" in text, f"{agent}: does not name the schema it is validated against"
    assert "Return ONLY a JSON object" in text, f"{agent}: missing native-JSON directive"

    example = _extract_example_json(text)
    schema = _load_hc().CATEGORY_SCHEMA

    for key in schema["required"]:
        assert key in example, f"{agent}: missing required top-level key {key!r}"
    assert isinstance(example["score"], (int, float)) and 0 <= example["score"] <= 10
    assert isinstance(example["summary"], str) and example["summary"]
    assert isinstance(example["findings"], list)

    finding_required = schema["properties"]["findings"]["items"]["required"]
    for i, finding in enumerate(example["findings"]):
        for key in finding_required:
            assert key in finding, f"{agent}.findings[{i}]: missing {key!r}"
        assert finding["severity"] in {"critical", "warning", "info", "uncertain"}
        assert isinstance(finding["description"], str) and finding["description"]


def test_strategy_agent_example_conforms_to_strategy_schema():
    path = AGENTS_DIR / "health-strategy.md"
    assert path.exists(), f"{path} missing"
    text = path.read_text()
    assert "STRATEGY_SCHEMA" in text, "strategy agent does not name the schema it is validated against"
    assert "Return ONLY a JSON object" in text, "strategy agent missing native-JSON directive"

    example = _extract_example_json(text)
    schema = _load_hc().STRATEGY_SCHEMA

    for key in schema["required"]:
        assert key in example, f"strategy example: missing required top-level key {key!r}"
    assert example["verdict"] in schema["properties"]["verdict"]["enum"]
    assert example["confidence"] in schema["properties"]["confidence"]["enum"]
    assert isinstance(example["summary"], str) and example["summary"]
    assert isinstance(example["sections"], list) and example["sections"]

    section_required = schema["properties"]["sections"]["items"]["required"]
    for i, section in enumerate(example["sections"]):
        for key in section_required:
            assert key in section, f"strategy.sections[{i}]: missing {key!r}"
        assert section["heading"].strip()
        assert section["body"].strip()
