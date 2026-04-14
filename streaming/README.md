# Birdcam Streaming Setup

Transcodes the Reolink RLC-811A RTSP stream to HLS on a Raspberry Pi and exposes it via Tailscale Funnel for playback on jlav.io.

```
Reolink (192.168.1.21) --RTSP--> Pi (FFmpeg -> HLS) --Tailscale Funnel--> jlav.io/birds
```

## Prerequisites

- Raspberry Pi on the same LAN as the camera
- Tailscale installed on the Pi (`curl -fsSL https://tailscale.com/install.sh | sh`)
- Tailscale Funnel enabled for your tailnet (admin console -> DNS -> Enable HTTPS)

## 1. Configure the camera

Open `http://192.168.1.21:9000` in a browser (or the Reolink app):

1. Set an admin password
2. Verify RTSP is enabled (Settings -> Network -> Advanced -> Port -> RTSP port 554)
3. Set the stream to H.264 if possible (Settings -> Display -> Encode -> H.264) -- this avoids re-encoding on the Pi
4. Adjust zoom/focus to frame the bird feeder

Test the stream from any machine on your LAN:

```bash
ffplay rtsp://admin:YOUR_PASSWORD@192.168.1.21:554//h264Preview_01_main
```

## 2. Install dependencies on the Pi

```bash
sudo apt update && sudo apt install -y ffmpeg nginx
```

## 3. Deploy the streaming files

```bash
# Copy files to the Pi
scp streaming/start_stream.sh pi@<pi-ip>:/opt/birdcam/start_stream.sh
scp streaming/nginx-hls.conf pi@<pi-ip>:/tmp/nginx-hls.conf

# On the Pi:
sudo mkdir -p /opt/birdcam /var/www/hls /etc/birdcam
sudo chmod +x /opt/birdcam/start_stream.sh

# Nginx config
sudo cp /tmp/nginx-hls.conf /etc/nginx/sites-available/hls
sudo ln -sf /etc/nginx/sites-available/hls /etc/nginx/sites-enabled/hls
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx
```

## 4. Create the environment file

```bash
sudo tee /etc/birdcam/env << 'EOF'
REOLINK_RTSP_URL=rtsp://admin:YOUR_PASSWORD@192.168.1.21:554//h264Preview_01_main
HLS_DIR=/var/www/hls
VIDEO_CODEC=copy
EOF
sudo chmod 600 /etc/birdcam/env
```

If the camera outputs H.265, change `VIDEO_CODEC=copy` to `VIDEO_CODEC=libx264`. This will re-encode on the Pi (uses more CPU but necessary since browsers don't support HEVC in HLS).

## 5. Install and start the systemd service

```bash
sudo cp streaming/birdcam-stream.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now birdcam-stream
```

Check status:

```bash
sudo systemctl status birdcam-stream
journalctl -u birdcam-stream -f
```

Verify HLS is being served locally:

```bash
curl -s http://localhost:8080/stream.m3u8
```

## 6. Expose via Tailscale Funnel

```bash
sudo tailscale funnel --bg 8080
```

This produces a URL like `https://<pi-name>.<tailnet>.ts.net/`. Verify it works:

```bash
curl -s https://<pi-name>.<tailnet>.ts.net/stream.m3u8
```

## 7. Update Vercel

Set `BIRDCAM_STREAM_URL` in the Vercel dashboard to:

```
https://<pi-name>.<tailnet>.ts.net/stream.m3u8
```

No code changes needed in jlav.io. The existing passphrase gate and hls.js player work as-is.

## 8. Deploy the ML classification pipeline (Docker)

The inference worker, TorchServe, Catalog API, and PostgreSQL run as Docker containers on the Pi alongside the HLS streaming service.

### Install Docker on the Pi

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in, then verify:
docker --version
```

### Clone the repo and configure

```bash
cd ~/ml-bird-classification
cp .env.example .env
# Edit .env with your actual values:
#   REOLINK_RTSP_URL=rtsp://admin:YOUR_PASSWORD@192.168.1.21:554//h264Preview_01_main
#   EBIRD_API_KEY=your_key_here
#   EBIRD_REGION=US-WI
```

### Build and start the pipeline

```bash
docker compose -f docker-compose.pi.yml up -d --build
```

### Verify services

```bash
# Check all containers are running
docker compose -f docker-compose.pi.yml ps

# TorchServe health
curl http://localhost:8080/ping

# Catalog API health
curl http://localhost:8000/health

# Watch worker logs for detections
docker compose -f docker-compose.pi.yml logs -f worker
```

### Manage the pipeline

```bash
# Stop all services
docker compose -f docker-compose.pi.yml down

# Restart just the worker
docker compose -f docker-compose.pi.yml restart worker

# View recent logs
docker compose -f docker-compose.pi.yml logs --tail=100 worker
```

## Troubleshooting

**Stream not connecting**: Verify the camera is reachable (`ping 192.168.1.21`) and RTSP is enabled. Test with `ffplay` from the Pi.

**High CPU on Pi (H.265 re-encode)**: Switch the camera to H.264 output in its settings, then set `VIDEO_CODEC=copy` to avoid transcoding entirely.

**Stale segments**: The script cleans up on exit. If segments accumulate, `rm /var/www/hls/*.ts /var/www/hls/*.m3u8` and restart the service.

**Funnel not reachable**: Ensure Funnel is enabled in your Tailscale admin console and the Pi's Tailscale is logged in (`tailscale status`).
