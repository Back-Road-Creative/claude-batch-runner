#!/usr/bin/env bash
set -euo pipefail

# Compute the next available report path under a date-namespaced directory.
#
# Usage: next-report-path.sh BASE_DIR SLUG DATE TYPE
#   BASE_DIR — root reports directory (e.g. ./health-reports)
#   SLUG     — project slug (e.g. my-service)
#   DATE     — YYYY-MM-DD
#   TYPE     — report kind suffix (e.g. health, build, remediation)
#
# Behavior:
#   - Creates BASE_DIR/SLUG if it does not exist.
#   - If no report exists for the given (slug, date, type), returns the
#     unsuffixed path: BASE_DIR/SLUG/DATE-TYPE.md
#   - Otherwise returns the next-N suffix: BASE_DIR/SLUG/DATE-TYPE-N.md
#     where N is one greater than the highest existing suffix for that
#     (date, type). The unsuffixed file counts as N=1, so the second
#     report becomes -2, third -3, etc.
#
# Output: one absolute path on stdout. The file is NOT created — caller writes it.

if [[ "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: next-report-path.sh BASE_DIR SLUG DATE TYPE

Compute the next non-conflicting report path under BASE_DIR/SLUG/.
Pattern: DATE-TYPE.md, then DATE-TYPE-2.md, DATE-TYPE-3.md, ...
EOF
  exit 0
fi

if [[ $# -ne 4 ]]; then
  echo "ERROR: expected 4 args (BASE_DIR SLUG DATE TYPE), got $#. Use --help." >&2
  exit 1
fi

BASE_DIR="$1"
SLUG="$2"
DATE="$3"
TYPE="$4"

PROJ_DIR="$BASE_DIR/$SLUG"
mkdir -p "$PROJ_DIR"

UNSUFFIXED="$PROJ_DIR/$DATE-$TYPE.md"

# Find highest existing -N suffix for this (date, type), if any.
shopt -s nullglob
max_n=0
for f in "$PROJ_DIR/$DATE-$TYPE-"*.md; do
  base="${f##*/}"
  n="${base#$DATE-$TYPE-}"
  n="${n%.md}"
  if [[ "$n" =~ ^[0-9]+$ ]] && (( n > max_n )); then
    max_n=$n
  fi
done

# If neither the unsuffixed file nor any -N file exists, return unsuffixed.
if [[ ! -e "$UNSUFFIXED" ]] && (( max_n == 0 )); then
  echo "$UNSUFFIXED"
  exit 0
fi

# Otherwise emit -(max+1). The unsuffixed file (if present) counts as N=1.
if [[ -e "$UNSUFFIXED" ]] && (( max_n < 1 )); then
  max_n=1
fi
next=$(( max_n + 1 ))
echo "$PROJ_DIR/$DATE-$TYPE-$next.md"
