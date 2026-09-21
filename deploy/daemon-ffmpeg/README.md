# ffmpeg for the Dify plugin daemon

The yt-dlp plugin converts videos to mp3 with `--extract-audio`, which requires
ffmpeg + ffprobe inside the **plugin daemon** container. The daemon image does
not ship them, so audio extraction fails with:

```
ERROR: Postprocessing: ffprobe and ffmpeg not found. Please install or provide the path using --ffmpeg-location
```

Run these steps **on the Docker host** (192.168.2.105), in the directory
containing Dify's `docker-compose.yaml` (usually `dify/docker/`).

## 1. Get static ffmpeg binaries

Pick the download by the **container's** architecture, not the host's:

```bash
mkdir -p ffmpeg/bin && cd ffmpeg
# check the daemon container arch first:
docker compose exec plugin_daemon uname -m
#   aarch64 -> use ffmpeg-release-arm64-static.tar.xz
#   x86_64  -> use ffmpeg-release-amd64-static.tar.xz

curl -LO https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz
tar xf ffmpeg-release-*.tar.xz
cp ffmpeg-release-*-static/ffmpeg ffmpeg-release-*-static/ffprobe bin/
cd ..
```

## 2. Mount them into the daemon

Merge the `volumes:` block from `docker-compose.override.yaml` (this folder)
into your `docker-compose.override.yaml` — it expects `ffmpeg/bin/` from step 1
to sit next to the compose files. Then recreate the daemon:

```bash
docker compose up -d plugin_daemon
```

(Alternative instead of steps 1–2: build a derived image —
`FROM <your daemon image>` + `RUN apt-get update && apt-get install -y ffmpeg`
— and point the compose `image:` at it.)

## 3. Verify

```bash
docker compose exec plugin_daemon ffmpeg -version | head -1
docker compose exec plugin_daemon ffprobe -version | head -1
```

Both should print a version. Then re-run the workflow with
`extract_audio=true` on any public video — it should now return an mp3.

## Why not bundle ffmpeg inside the plugin?

Static ffmpeg + ffprobe add ~55 MB compressed; the plugin package is already
41 MB against the ~50 MB plugin-daemon upload cap. Installing them in the
daemon container fixes all tools at once and keeps the plugin installable.
