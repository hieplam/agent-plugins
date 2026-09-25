#!/usr/bin/env bash
# setup_remotion.sh — copy the bundled Remotion template into a project working
# dir and install deps once. Idempotent: skips npm install if node_modules exists.
#
# Usage: setup_remotion.sh <skill-dir> <work-dir>
#   <skill-dir>  the skill root (the dir containing remotion-template/)
#   <work-dir>   where to build the project (e.g. projects/<name>/_remotion)
set -euo pipefail
SKILL_DIR="${1:?skill dir required}"
WORK="${2:?work dir required}"

mkdir -p "$WORK"
cp -R "$SKILL_DIR/remotion-template/." "$WORK/"
mkdir -p "$WORK/public"

if [ ! -d "$WORK/node_modules" ]; then
  echo "installing Remotion deps in $WORK (one-time, a few minutes)..."
  ( cd "$WORK" && npm install --no-audit --no-fund )
fi
echo "remotion project ready: $WORK"
echo "drop the image at: $WORK/public/  and render with --props=<props.json>"
