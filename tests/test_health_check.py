"""Tests for the worked example in examples/health-check/.

Agent calls are always injected mocks, so the suite never subprocesses
`claude` and CI spends nothing. Fan-out, score aggregation, coverage-gap
handling, and report formatting are exercised against synthetic envelopes
shaped like real `--json-schema` structured output.

The two shell steps (compute-health-score.sh, next-report-path.sh) are NOT
mocked — they run for real, because their arithmetic and their
non-colliding-filename logic are exactly the parts worth testing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "examples/health-check/health-check.py"


def _load_health_check():
    spec = importlib.util.spec_from_file_location("health_check", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hc():
    return _load_health_check()


# --- parse_categories ---------------------------------------------------------


@pytest.mark.parametrize(
    "arg,expected",
    [
        (
            "all",
            ["gaps", "flaws", "completeness", "complexity", "security", "quality", "production"],
        ),
        ("", ["gaps", "flaws", "completeness", "complexity", "security", "quality", "production"]),
        ("1,3,5", ["gaps", "completeness", "security"]),
        ("gaps,security", ["gaps", "security"]),
        ("1,gaps,1", ["gaps"]),
        ("Security, 1", ["security", "gaps"]),
    ],
)
def test_parse_categories_valid(hc, arg, expected):
    assert hc.parse_categories(arg) == expected


@pytest.mark.parametrize("arg", ["8", "0", "bogus", "1,99"])
def test_parse_categories_invalid(hc, arg):
    with pytest.raises(SystemExit):
        hc.parse_categories(arg)


# --- fan_out_categories -------------------------------------------------------


def _envelope(structured: dict) -> dict:
    return {"is_error": False, "result": "ok", "structured_output": structured}


def test_fan_out_aggregates_per_category(hc):
    def fake_call(agent, prompt, schema, budget):
        cat = agent.removeprefix("health-")
        return _envelope({"score": 7.5, "summary": f"{cat} ok", "findings": []})

    out = hc.fan_out_categories(
        ["gaps", "security"], Path("/tmp"), "briefing", 0.15, call=fake_call
    )
    assert set(out.keys()) == {"gaps", "security"}
    assert out["gaps"]["structured_output"]["score"] == 7.5
    assert out["security"]["structured_output"]["summary"] == "security ok"


def test_fan_out_surfaces_agent_error_as_coverage_gap(hc):
    def flaky(agent, prompt, schema, budget):
        if agent == "health-security":
            raise hc.AgentError("budget exceeded")
        return _envelope({"score": 8, "summary": "ok", "findings": []})

    out = hc.fan_out_categories(["gaps", "security"], Path("/tmp"), "briefing", 0.15, call=flaky)
    assert "_error" in out["security"]
    assert "budget exceeded" in out["security"]["_error"]
    assert "structured_output" in out["gaps"]


def test_fan_out_empty_returns_empty(hc):
    assert hc.fan_out_categories([], Path("/tmp"), "briefing", 0.15) == {}


# --- compute_score (real subprocess to compute-health-score.sh) ---------------


def test_compute_score_skips_coverage_gaps(hc):
    outputs = {
        "security": _envelope({"score": 9, "summary": "s", "findings": []}),
        "flaws": {"_error": "agent failed"},
        "quality": _envelope({"score": 6, "summary": "q", "findings": []}),
    }
    score = hc.compute_score(outputs)
    assert score["categories_scored"] == 2
    assert "security" in score["weights"]
    assert "flaws" not in score["weights"]
    assert "quality" in score["weights"]


def test_compute_score_no_inputs(hc):
    score = hc.compute_score({"flaws": {"_error": "x"}})
    assert score["categories_scored"] == 0
    assert score["overall"] is None


# --- write_report -------------------------------------------------------------


def test_write_report_includes_verdict_scores_findings(hc, tmp_path):
    outputs = {
        "gaps": _envelope(
            {
                "score": 7,
                "summary": "Sparse tests.",
                "findings": [
                    {
                        "severity": "warning",
                        "file": "src/a.py",
                        "line": 12,
                        "function": "do_thing",
                        "description": "No test coverage.",
                        "fix": "Add unit tests.",
                    }
                ],
            }
        ),
        "security": _envelope({"score": 9, "summary": "Clean.", "findings": []}),
        "flaws": {"_error": "agent failed"},
    }
    score = {
        "overall": 8.0,
        "categories_scored": 2,
        "weights": {
            "gaps": {"score": 7, "weight": 1.0, "weighted": 7.0},
            "security": {"score": 9, "weight": 2.0, "weighted": 18.0},
        },
        "total_weight": 3.0,
        "total_weighted": 25.0,
    }

    strategy = {
        "verdict": "INCREMENTAL",
        "confidence": "high",
        "summary": "Tighten test coverage; flaws agent was a coverage gap.",
        "sections": [
            {
                "heading": "Rationale",
                "body": "**Priority order:** test coverage first.\n\n**Watch for:** flaws agent failure.",
            },
            {
                "heading": "Finding Clusters",
                "body": "| Cluster | Files | Hits | Assessment |\n|---|---|---|---|\n| Tests | src/a.py | 1 | independent |",
            },
        ],
    }
    report = hc.write_report(
        slug="example",
        report_dir=tmp_path,
        today="2026-05-22",
        strategy=strategy,
        category_outputs=outputs,
        score=score,
        cwd=Path("/tmp/example"),
    )
    text = report.read_text()
    assert "# Project Health Report: example" in text
    assert "## Strategy Verdict: INCREMENTAL" in text
    assert "**Confidence:** high" in text
    assert "### Rationale" in text
    assert "### Finding Clusters" in text
    assert "**Overall** | **8.0/10**" in text
    assert "src/a.py:12" in text
    assert "do_thing" in text
    assert "**Fix:** Add unit tests." in text
    assert "_Coverage gap:_" in text


def test_write_report_no_findings(hc, tmp_path):
    outputs = {"gaps": _envelope({"score": 10, "summary": "Perfect.", "findings": []})}
    score = {
        "overall": 10.0,
        "categories_scored": 1,
        "weights": {"gaps": {"score": 10, "weight": 1.0, "weighted": 10.0}},
        "total_weight": 1.0,
        "total_weighted": 10.0,
    }
    report = hc.write_report(
        slug="clean",
        report_dir=tmp_path,
        today="2026-05-22",
        strategy={
            "verdict": "INCREMENTAL",
            "confidence": "high",
            "summary": "Nothing to do.",
            "sections": [],
        },
        category_outputs=outputs,
        score=score,
        cwd=Path("/tmp"),
    )
    text = report.read_text()
    assert "Gaps" in text
    assert "10/10" in text
    assert "## Strategy Verdict: INCREMENTAL" in text


def test_write_report_empty_strategy_renders_placeholder(hc, tmp_path):
    """call_strategy returns {} when the agent envelope had no structured_output;
    write_report must not crash and the report should flag the missing verdict."""
    outputs = {"gaps": _envelope({"score": 8, "summary": "ok", "findings": []})}
    score = {
        "overall": 8.0,
        "categories_scored": 1,
        "weights": {"gaps": {"score": 8, "weight": 1.0, "weighted": 8.0}},
        "total_weight": 1.0,
        "total_weighted": 8.0,
    }
    report = hc.write_report(
        slug="empty",
        report_dir=tmp_path,
        today="2026-05-22",
        strategy={},
        category_outputs=outputs,
        score=score,
        cwd=Path("/tmp"),
    )
    text = report.read_text()
    assert "## Strategy Verdict" in text
    assert "no structured output" in text


# --- call_strategy ------------------------------------------------------------


def test_call_strategy_returns_structured_output(hc):
    captured: dict = {}

    def fake_call(agent, prompt, schema, budget):
        captured["agent"] = agent
        captured["schema"] = schema
        return {
            "is_error": False,
            "structured_output": {
                "verdict": "CONSOLIDATE",
                "confidence": "medium",
                "summary": "Three findings share a missing-validation root cause.",
                "sections": [
                    {"heading": "Rationale", "body": "**Root cause:** absent input validation."},
                ],
            },
        }

    aggregated = {"gaps": {"structured_output": {"score": 7, "summary": "s", "findings": []}}}
    result = hc.call_strategy(aggregated, Path("/tmp"), "briefing", 0.25, call=fake_call)

    assert captured["agent"] == "health-strategy"
    assert captured["schema"] is hc.STRATEGY_SCHEMA
    assert result["verdict"] == "CONSOLIDATE"
    assert result["confidence"] == "medium"
    assert result["sections"][0]["heading"] == "Rationale"


def test_call_strategy_missing_structured_output_returns_empty(hc):
    def fake_call(agent, prompt, schema, budget):
        return {"is_error": False, "result": "agent forgot to emit JSON"}

    result = hc.call_strategy({}, Path("/tmp"), "briefing", 0.25, call=fake_call)
    assert result == {}
