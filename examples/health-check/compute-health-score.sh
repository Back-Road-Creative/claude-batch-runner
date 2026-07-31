#!/usr/bin/env bash
set -euo pipefail

# Compute weighted health score from category scores.
# Usage: compute-health-score.sh --security N --flaws N --production N --gaps N --completeness N --complexity N --quality N
# All scores are 0-10 integers or decimals.
# Missing categories are excluded from the weighted average (not zero-filled).
# Output: JSON to stdout.

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: compute-health-score.sh [--category score]..."
  echo "Categories: --security, --flaws, --production, --gaps, --completeness, --complexity, --quality"
  echo "Scores: 0-10 (integer or decimal)"
  echo "Output: JSON with weighted overall score and breakdown."
  exit 0
fi

# Collect arguments into environment variables for Python
SCORE_SECURITY=""
SCORE_FLAWS=""
SCORE_PRODUCTION=""
SCORE_GAPS=""
SCORE_COMPLETENESS=""
SCORE_COMPLEXITY=""
SCORE_QUALITY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --security)     SCORE_SECURITY="$2"; shift 2 ;;
    --flaws)        SCORE_FLAWS="$2"; shift 2 ;;
    --production)   SCORE_PRODUCTION="$2"; shift 2 ;;
    --gaps)         SCORE_GAPS="$2"; shift 2 ;;
    --completeness) SCORE_COMPLETENESS="$2"; shift 2 ;;
    --complexity)   SCORE_COMPLEXITY="$2"; shift 2 ;;
    --quality)      SCORE_QUALITY="$2"; shift 2 ;;
    *)
      echo "ERROR: Unknown argument '$1'. Use --help for usage." >&2
      exit 1
      ;;
  esac
done

export SCORE_SECURITY SCORE_FLAWS SCORE_PRODUCTION SCORE_GAPS SCORE_COMPLETENESS SCORE_COMPLEXITY SCORE_QUALITY

python3 <<'PYEOF'
import json, os

WEIGHTS = {
    "security": 2.0,
    "flaws": 1.5,
    "production": 1.2,
    "gaps": 1.0,
    "completeness": 1.0,
    "complexity": 1.0,
    "quality": 0.8,
}

scores = {}
for cat, weight in WEIGHTS.items():
    val = os.environ.get(f"SCORE_{cat.upper()}", "")
    if val:
        scores[cat] = float(val)

if not scores:
    print(json.dumps({"error": "No category scores provided. Use --help for usage."}))
    raise SystemExit(1)

weights_detail = {}
total_weight = 0.0
total_weighted = 0.0

for cat, score in scores.items():
    w = WEIGHTS[cat]
    weighted = round(score * w, 2)
    weights_detail[cat] = {"score": score, "weight": w, "weighted": weighted}
    total_weight += w
    total_weighted += weighted

overall = round(total_weighted / total_weight, 1)

print(json.dumps({
    "overall": overall,
    "categories_scored": len(scores),
    "weights": weights_detail,
    "total_weight": total_weight,
    "total_weighted": round(total_weighted, 2),
}, indent=2))
PYEOF
