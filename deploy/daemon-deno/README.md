# deno for the Dify plugin daemon

yt-dlp prefers deno over the plugin's bundled QuickJS whenever it is on PATH
(deno is the default-enabled, highest-priority runtime). Installing it in the
**plugin daemon** container gives YouTube extraction the most capable runtime —
same mechanism as the ffmpeg deployment in `../daemon-ffmpeg/`.

Run these steps **on the Docker host** (192.168.2.105), in the directory
containing Dify's `docker-compose.yaml` (usually `dify/docker/`).

## 1. Download deno for the daemon's architecture

Check the container arch first, then fetch the matching build:

```bash
docker compose exec plugin_daemon uname -m
#   aarch64 -> deno-aarch64-unknown-linux-gnu.zip
#   x86_64  -> deno-x86_64-unknown-linux-gnu.zip

mkdir -p deno-bin && cd deno-bin
curl -LO https://github.com/denoland/deno/releases/latest/download/deno-aarch64-unknown-linux-gnu.zip
python3 -m zipfile -e deno-aarch64-unknown-linux-gnu.zip .
chmod +x den
./den --version   # sanity check on the host
cd ..
```

## 2. Mount it into the daemon

Merge the `volumes:` block from `docker-compose.override.yaml` (this folder)
into your `docker-compose.override.yaml` — it expects `deno-bin/deno` from
step 1 next to the compose files. Then recreate the daemon:

```bash
docker compose up -d plugin_daemon
```

## 3. Verify

```bash
docker compose exec plugin_daemon deno --version | head -1
```

Then re-run the workflow. With deno present, the plugin automatically prefers
it (no plugin change needed); the bundled QuickJS stays as the fallback.

## Why deno too, when QuickJS is bundled?

QuickJS solves the standard signature challenges offline. Deno is yt-dlp's
reference runtime — first to receive support for new YouTube challenge types.
With both deployed: deno first, QuickJS fallback. If deno is ever unwanted,
remove the override block and `docker compose up -d plugin_daemon` again.
