#!/bin/bash
# iermu_rec.sh [SECONDS] [OUT.mp4]
# Record the iermu camera's RTMP stream, strip the vendor trailers with
# fix_flv.py, and save a normal MP4 (H.264 1280x720 + AAC mono).
cd "$(dirname "$0")" || exit 9
SECS=${1:-30}
OUT=${2:-iermu_$(date +%Y%m%d_%H%M%S).mp4}
IMG=owntone/owntone:latest
URL=rtmp://192.168.10.34:1935/live/live
for i in 1 2 3 4 5 6; do
  rm -f "$OUT"
  docker run --rm --network host --entrypoint ffmpeg "$IMG" -hide_banner -nostdin -loglevel fatal \
      -rw_timeout 5000000 -i "$URL" -t "$SECS" -c copy -f flv - \
    | python3 fix_flv.py - - \
    | docker run --rm -i --user "$(id -u):$(id -g)" -v "$PWD":/out --entrypoint ffmpeg "$IMG" \
      -hide_banner -loglevel error -i - -c copy -movflags +faststart -y "/out/$OUT"
  if [ -s "$OUT" ]; then
    echo "saved: $PWD/$OUT"
    exit 0
  fi
  echo "no stream (try $i/6); the camera may still be busy, retrying in 30 s" >&2
  sleep 30
done
echo "gave up" >&2
exit 1
