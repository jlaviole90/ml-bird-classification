#!/usr/bin/env bash
set -euo pipefail

# ── Configuration (override via environment) ─────────────────────────────────
RTSP_URL="${REOLINK_RTSP_URL:?Set REOLINK_RTSP_URL e.g. rtsp://admin:pass@192.168.1.21:554//h264Preview_01_main}"
HLS_DIR="${HLS_DIR:-/var/www/hls}"
HLS_SEGMENT_SEC="${HLS_SEGMENT_SEC:-4}"
HLS_LIST_SIZE="${HLS_LIST_SIZE:-10}"

# H.265 cameras need re-encoding for browser HLS playback.
# Set to "copy" if the camera is configured to output H.264.
VIDEO_CODEC="${VIDEO_CODEC:-copy}"

# ── Setup ────────────────────────────────────────────────────────────────────
mkdir -p "$HLS_DIR"

cleanup() {
    echo "[birdcam] Stopping FFmpeg (pid $FFPID)..."
    kill "$FFPID" 2>/dev/null || true
    wait "$FFPID" 2>/dev/null || true
    rm -f "$HLS_DIR"/*.ts "$HLS_DIR"/*.m3u8
    echo "[birdcam] Cleaned up."
}
trap cleanup EXIT INT TERM

# ── Main loop (reconnects on stream drop) ────────────────────────────────────
while true; do
    echo "[birdcam] Connecting to $RTSP_URL ..."

    ffmpeg -hide_banner -loglevel warning \
        -rtsp_transport tcp \
        -i "$RTSP_URL" \
        -c:v "$VIDEO_CODEC" \
        -an \
        -f hls \
        -hls_time "$HLS_SEGMENT_SEC" \
        -hls_list_size "$HLS_LIST_SIZE" \
        -hls_flags delete_segments+append_list \
        -hls_segment_filename "$HLS_DIR/segment_%03d.ts" \
        "$HLS_DIR/stream.m3u8" &

    FFPID=$!
    echo "[birdcam] FFmpeg started (pid $FFPID)"

    wait "$FFPID" || true
    echo "[birdcam] FFmpeg exited — reconnecting in 5s..."
    sleep 5
done
