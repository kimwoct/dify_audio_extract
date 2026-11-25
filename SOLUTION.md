# dify_audio_extract — fix solution (plugin v0.0.6)

Incident: Dify tool `kimwxng/yt-dlp/yt-dlp` failed with
`RuntimeError: yt-dlp failed with exit code 1 … This video is not available`
plus `WARNING: [youtube] No supported JavaScript runtime could be found`.

## Root causes (in the order they were found)

1. **Stale extractor + missing JS runtime.** The plugin bundled yt-dlp
   2026.03.03. Since yt-dlp 2025.11.12, YouTube extraction requires an external
   JavaScript runtime (EJS challenges); the plugin daemon container has none,
   and modern videos fail with "This video is not available" without one.
   (Ancient videos like "Me at the zoo" extract with no runtime — a false
   positive when testing.)
2. **Upgrades not landing.** Plugin 0.0.2/0.0.3 added a bundled QuickJS-NG
   runtime, but the daemon kept executing 0.0.1, so the warning persisted and
   upgrades were invisible. Fixed in 0.0.4 by embedding a `JS runtime: …`
   status marker in every error message — no marker ⇒ old plugin still
   installed.
3. **noexec storage volume.** After 0.0.4 proved the flag was attached, yt-dlp
   still couldn't use the bundled qjs: the daemon's `/app/storage` volume is
   mounted noexec (exec bit is not enough there). The yt-dlp binary had a
   copy-to-tmp fallback; qjs had none. 0.0.5 stages qjs to an executable temp
   dir on Linux, mirroring that fallback.
4. **Unplayable video (red herring).** `MQt3wXPRGRs` reported UNPLAYABLE by
   every YouTube client at incident time (not region-blocked; oEmbed still
   alive). It later became downloadable again with the refreshed extractor.
5. **ffmpeg absent from the daemon** (separate, still relevant): mp3 conversion
   (`extract_audio=true`) hard-fails without ffmpeg/ffprobe in the daemon
   container — `ERROR: Postprocessing: ffprobe and ffmpeg not found`.

## Server-side cookie fallback in plugin 0.0.8

When no `cookies_file` is supplied, the plugin reads the private file at
`/etc/dify/yt-dlp/youtube-cookies.txt`, or the path configured by
`YT_DLP_COOKIES_FILE` in the plugin process environment. It validates and
stages a private copy without modifying the source. Explicit files retain
precedence. Cookies remain outside Git and the plugin package; `secrets/`
is excluded from both. See `deploy/daemon-cookies/README.md` for the read-only
directory mount and workflow migration, and [COOKIE_API.md](COOKIE_API.md)
for an API request with no cookie upload. All fallback calls share one
server-side session; daemon administrators can still access the source file.

## Plugin 0.0.9 — stale extractor breaks authenticated sessions (2026-11-25)

Incident: `RuntimeError: yt-dlp failed with exit code 1` carrying
`[jsc:quickjs] … TypeError: not a function`, `n challenge solving failed`, the
SABR-only warning, and `ERROR: [youtube] <id>: The page needs to be reloaded.`
The server-side cookie file was valid and the staged QuickJS runtime was running.

Root cause: with YouTube auth cookies present, bundled yt-dlp 2026.08.19 selects
`_DEFAULT_AUTHED_CLIENTS = ('web_embedded', 'tv_downgraded', 'web')` and the
`main` player JS variant. For the reported player (`b485e332-main`), the EJS
0.8.0 n-challenge solver crashes under QuickJS-NG 0.17.0
(`TypeError: not a function`), so the `web_embedded` URLs stay SABR-only. The
remaining client, `tv_downgraded`, returns playability status `ERROR` with
reason "The page needs to be reloaded", which becomes the final error. Without
cookies, the cookie-free clients (`visionos`, `web`) extract the same video —
that is why cookie-free checks passed.

yt-dlp 2026.11.23 changes both paths:

- the default player JS variant is `tv`, not `main`, so the solver no longer
  crashes on the `-main` player;
- authenticated defaults are `('tv_samsung', 'web_embedded', 'web')`;
  `tv_downgraded` is dropped;
- the STS truncation workaround and the 2026.10.21 certifi/brotli security
  fixes are included.

Reproduced on the bundled Linux zipapp + bundled aarch64 QuickJS under Python
3.12 in Docker with
`--extractor-args youtube:player_client=web_embedded,tv_downgraded,web`:
2026.08.19 fails with the reported error; 2026.11.23 lists formats and the
plugin's selector picks `18` (640x360 mp4). The same client set succeeds with
the refreshed macOS binary. This is not a defect in cookie validation, cookie
staging, or QuickJS staging.

Note: the `TypeError` is printed before the `input = NChallengeInput(...)` line,
so the plugin's 1000-char `stderr_tail` can hide it. The reliable markers in
this release are `n challenge solving failed` and `The page needs to be reloaded`.

## Cookie handling update in plugin 0.0.7

The existing `cookies_file` input is unchanged. Cookie contents are now
validated and normalized before launching yt-dlp, including local files,
which are copied rather than handed directly to yt-dlp for modification.
Errors distinguish missing cookies from supplied cookies. Cookie files are
excluded from git and plugin packaging. See [COOKIE_API.md](COOKIE_API.md)
for the upload-then-run workflow API request; credentials are never embedded
in the package. Valid file format cannot repair a rotated YouTube session.

## Fixes shipped in plugin 0.0.6

- Bundled **QuickJS-NG v0.17.0** per-arch (`binary/{macosx,linux}/{aarch64,x86_64}/qjs`;
  linux builds are static-pie, no glibc dependency). sha256 pins and update
  recipe in [binary/RUNTIMES.md](binary/RUNTIMES.md).
- Bundled yt-dlp refreshed **2026.03.03 → 2026.08.19** (macos standalone +
  linux zipapp).
- `tools/yt-dlp.py`:
  - resolves the bundled qjs per platform/arch, chmods and strips quarantine;
  - passes `--js-runtimes quickjs:<path>` — a host deno still wins
    automatically (yt-dlp priority: deno > node > quickjs > bun);
  - on Linux, stages qjs into an executable temp dir before invoking yt-dlp
    (noexec-volume proof);
  - every `RuntimeError` now carries `JS runtime: <status>` for instant
    old-plugin detection.
- optional `cookies_file` accepts a user-provided Netscape-format `cookies.txt`
  upload, stages it with mode `0600` for the yt-dlp process, and removes the
  temporary copy after the process exits; the plugin does not read browser
  profiles or log cookie contents.
- Package: ~41 MB compressed / ~48 MB uncompressed (packager cap: 50 MiB
  uncompressed).

## Deployment on the Dify host (192.168.2.105)

1. **Plugin**: Dify → Plugins → yt-dlp → Remove → Confirm → Install plugin →
   Install from local file → `dify_audio_extract.difypkg` (0.0.9).
2. **Daemon mounts** (optional but recommended; no plugin change needed):
   - deno (yt-dlp's reference runtime): [deploy/daemon-deno/](deploy/daemon-deno/README.md)
   - ffmpeg + ffprobe (mp3 conversion): [deploy/daemon-ffmpeg/](deploy/daemon-ffmpeg/README.md)

   Merge both overrides' `volumes:` blocks into one
   `docker-compose.override.yaml` next to Dify's compose file, then
   `docker compose up -d plugin_daemon`.
3. **Verify**:
   ```bash
   docker compose exec plugin_daemon deno --version
   docker compose exec plugin_daemon ffmpeg -version | head -1
   ```
   then run the workflow with `extract_audio=true` on any public video.

## Troubleshooting quick reference

| Error signature | Meaning / fix |
|---|---|
| Error has **no** `JS runtime:` marker | Old plugin (≤0.0.3) still installed — redo Remove + Install. |
| Marker present **and** "No supported JavaScript runtime" in stderr | qjs couldn't exec (noexec) — install ≥0.0.5 or add the deno mount. |
| `ffprobe and ffmpeg not found` | Daemon lacks ffmpeg — apply `deploy/daemon-ffmpeg/`. |
| `Sign in to confirm you’re not a bot` | Upload a current YouTube `cookies.txt` through the tool's `cookies_file` input. Never paste cookie values into chat. |
| "This video is not available", marker present, no JS warning | Video is genuinely unavailable (private/removed/restricted) — check logged-out in a browser. |
| `n challenge solving failed` **and** `The page needs to be reloaded` | Bundled yt-dlp ≤ 2026.08.19 with auth cookies (`web_embedded`/`tv_downgraded`) — install ≥ 0.0.9 (yt-dlp 2026.11.23). |
| Plugin page version ≠ manifest `version:` | Wrong/failed install — repackage and reinstall. |

## Maintenance

- Refresh yt-dlp ~monthly (YouTube extractor rot is the #1 failure mode);
  [binary/RUNTIMES.md](binary/RUNTIMES.md) has download commands + sha256 pins.
- Refresh QuickJS-NG occasionally (rarely needed).
- Keep the uncompressed package under 50 MiB; if `yt-dlp_macos` (37 MB) ever
  pushes past it, swap it for the 3 MB zipapp (needs host python3 ≥ 3.11).
- Repackage: bump `version:` in `manifest.yaml`, then from the parent dir:
  `dify plugin package dify_audio_extract`.
