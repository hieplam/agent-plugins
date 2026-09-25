#!/usr/bin/env bash
# assemble.sh — turn a short seamless loop clip + a music file into the final
# long video: loop the clip to the target duration, loop the music to match,
# and encode at a chosen bitrate.
#
# Two encode modes:
#   copy  — stream-copy the loop clip (MAX quality, but file size = clip
#           bitrate × duration; a high-bitrate 1080p loop → ~9GB for 1 hour).
#   <Mbps> — re-encode video at that bitrate (e.g. 5 → ~2GB for 1 hour).
#            Use this when the file must be upload-friendly.
#
# Usage:
#   assemble.sh <loop.mp4> <music.(mp3|wav|m4a|flac)> <out.mp4> <seconds> <copy|MBPS>
#
# Examples:
#   assemble.sh loop.mp4 song.mp3 final_1h.mp4 3600 copy     # 1 hour, max quality
#   assemble.sh loop.mp4 song.mp3 final_1h.mp4 3600 5        # 1 hour, ~5 Mbps (~2GB)
set -euo pipefail
LOOP="${1:?loop clip required}"
MUSIC="${2:?music file required}"
OUT="${3:?output path required}"
SECS="${4:?target duration in seconds required}"
MODE="${5:?mode: 'copy' or a bitrate in Mbps (e.g. 5)}"

# NOTE: -map 1:a:0 explicitly grabs the first AUDIO stream — many mp3s carry an
# embedded cover-art PNG as a second stream that would otherwise break -map 1:a.
if [ "$MODE" = "copy" ]; then
  VOPTS=(-c:v copy)
else
  VOPTS=(-c:v libx264 -preset medium -b:v "${MODE}M" -maxrate "${MODE}M" -bufsize "$(( MODE * 2 ))M" -pix_fmt yuv420p)
fi

ffmpeg -y \
  -stream_loop -1 -i "$LOOP" \
  -stream_loop -1 -i "$MUSIC" \
  -map 0:v:0 -map 1:a:0 \
  "${VOPTS[@]}" \
  -c:a aac -b:a 192k \
  -t "$SECS" -movflags +faststart \
  "$OUT"

echo "done: $OUT"
ls -lh "$OUT"
