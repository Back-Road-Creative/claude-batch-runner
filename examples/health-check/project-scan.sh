#!/usr/bin/env bash
set -euo pipefail

# Print a structured project briefing block for the given directory.
# health-check.py runs this once and pastes the result into every category
# agent's prompt, so seven agents share one filesystem scan instead of
# re-deriving the same facts seven times.
#
# Output: stdout, plain-text briefing block. Each line is "key: value" or
# a short section header. Designed to be wrapped in a data-only frame
# before being passed to subagents.

if [[ "${1:-}" == "--help" ]] || [[ -z "${1:-}" ]]; then
  cat <<EOF
Usage: project-scan.sh DIR

Print a structured briefing block for the project at DIR.
Includes: slug, language, file counts, configs, CI, tests, recent commits.
EOF
  [[ "${1:-}" == "--help" ]] && exit 0 || exit 1
fi

# Resolve before taking the basename: `project-scan.sh .` would otherwise report
# a slug of "." and file the report under a directory named ".".
cd "$1"
DIR="$PWD"

SLUG="$(basename "$DIR")"

# Detect primary language by config file.
LANG="unknown"
if   [[ -f pyproject.toml ]];  then LANG="python (pyproject.toml)"
elif [[ -f setup.py ]];        then LANG="python (setup.py)"
elif [[ -f package.json ]];    then LANG="javascript/typescript (package.json)"
elif [[ -f Cargo.toml ]];      then LANG="rust (Cargo.toml)"
elif [[ -f go.mod ]];          then LANG="go (go.mod)"
elif [[ -f pom.xml ]];         then LANG="java (pom.xml)"
elif [[ -f Gemfile ]];         then LANG="ruby (Gemfile)"
fi

# Test framework hint
TESTS="none-detected"
if   [[ -d tests ]] || [[ -d test ]]; then TESTS="tests/ dir present"
elif [[ -f pytest.ini ]] || grep -q '\[tool\.pytest' pyproject.toml 2>/dev/null; then TESTS="pytest configured"
fi

# CI
CI="none"
if [[ -d .github/workflows ]]; then
  CI=".github/workflows/ ($(ls .github/workflows/ 2>/dev/null | wc -l) files)"
fi

# Counts (limit search depth to avoid traversing .venv/node_modules).
# Early-return when the base dir is missing — otherwise find writes "0" to
# stdout, then the caller's `|| echo 0` fallback also fires under pipefail,
# producing a double-zero "0\n0" in the captured value.
count_files() {
  local pattern="$1" base="${2:-.}"
  [[ -d "$base" ]] || { echo 0; return 0; }
  find "$base" -path '*/.venv' -prune -o \
               -path '*/node_modules' -prune -o \
               -path '*/.git' -prune -o \
               -path '*/__pycache__' -prune -o \
               -type f -name "$pattern" -print 2>/dev/null | wc -l
}

PY_SRC=$(count_files "*.py" src)
PY_TEST=$(count_files "*.py" tests)
JS_SRC=$(count_files "*.[jt]s" src)
SQL_FILES=$(count_files "*.sql" sql)
NB_FILES=$(count_files "*.py" notebooks)
ADR_FILES=$(count_files "*.md" docs/adr)
TOTAL_FILES=$(find . -path '*/.venv' -prune -o -path '*/node_modules' -prune -o -path '*/.git' -prune -o -type f -print 2>/dev/null | wc -l)

# Source layout (top 30 source files)
# Three pipefail-hostile spots:
#  - find exits non-zero when one of src/tests/sql/notebooks/docs is missing
#  - head -30 SIGPIPEs the upstream pipe (replaced with awk NR<=30)
#  - grep -v exits 1 when zero lines match (empty layout) — wrap in || true
# All three crash the script under `set -euo pipefail` on Bash 5.2+.
LAYOUT="$( { find src tests sql notebooks docs -maxdepth 3 -type f 2>/dev/null || true; } \
  | { grep -v '__pycache__' || true; } \
  | awk 'NR<=30' \
  | sed 's/^/  /' )"

# Recent commits
COMMITS="$(git log --oneline -10 2>/dev/null | sed 's/^/  /' || echo '  (no git history)')"

# Configs present
CONFIGS=()
for f in pyproject.toml setup.py package.json Cargo.toml go.mod tsconfig.json \
         Makefile docker-compose.yml Dockerfile .pre-commit-config.yaml \
         ARCHITECTURE.md README.md CLAUDE.md; do
  [[ -f "$f" ]] && CONFIGS+=("$f")
done
CONFIGS_STR="${CONFIGS[*]:-none}"

cat <<EOF
=== PROJECT BRIEFING ===
slug: $SLUG
path: $DIR
language: $LANG
total_files: $TOTAL_FILES
py_src_files: $PY_SRC
py_test_files: $PY_TEST
js_ts_src_files: $JS_SRC
sql_files: $SQL_FILES
notebook_files: $NB_FILES
adr_files: $ADR_FILES
tests: $TESTS
ci: $CI
configs: $CONFIGS_STR

source_layout (sample):
$LAYOUT

recent_commits:
$COMMITS
=== END BRIEFING ===
EOF
