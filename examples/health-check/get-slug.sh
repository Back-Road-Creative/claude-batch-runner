#!/usr/bin/env bash
set -euo pipefail

# Print the project slug for the current working directory (or arg 1 if given).
#
# The slug is the directory's basename, kept whole — any prefix the project
# uses ("svc-", "lib-", "pkg-", …) is part of the name, so it stays. Reports
# are filed under this slug, so two projects with the same basename in
# different parents will share a report directory; give them distinct
# directory names if that matters to you.
#
# Output: one line on stdout, no trailing decoration.

if [[ "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: get-slug.sh [DIR]

Print the project slug for DIR (default: \$PWD) — its basename, unmodified.
EOF
  exit 0
fi

target="${1:-$PWD}"
target="$(cd "$target" && pwd)"
basename "$target"
