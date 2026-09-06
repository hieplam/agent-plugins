#!/usr/bin/env bash
# calibrate_grid.sh — overlay a 10% grid on the image so you can read off
# normalized (0..1) coordinates for placing halos / auras / jiggle regions.
# Each cell = 10% of width/height. Read a target's center as (col/10, row/10).
#
# Usage: calibrate_grid.sh <input-image> <output-grid-png>
set -euo pipefail
IN="${1:?input image required}"
OUT="${2:?output png required}"
ffmpeg -y -i "$IN" -vf "drawgrid=w=iw/10:h=ih/10:t=2:c=red@0.7" "$OUT"
echo "grid written: $OUT  (each cell = 10%; read center as col/10, row/10)"
