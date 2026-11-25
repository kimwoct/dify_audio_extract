# Vendored binaries

Binaries bundled with this plugin and the releases they were taken from.
yt-dlp's YouTube extractor breaks frequently — refresh both on a schedule.

## yt-dlp — 2026.11.23

2026.11.23 is the first bundled release that extracts with the authenticated
client set (`tv_samsung`, `web_embedded`, `web`) and with the default `tv`
player JS variant. See "Plugin 0.0.9" in [SOLUTION.md](../SOLUTION.md) for the
incident this refresh fixes.

| File | Release asset | sha256 |
|---|---|---|
| `macosx/yt-dlp_macos` | [yt-dlp_macos](https://github.com/yt-dlp/yt-dlp/releases/download/2026.11.23/yt-dlp_macos) | `57654854886d0398a798895aadf5173e2749fbb5e249b9f3728022ab376e11fb` |
| `linux/yt-dlp` | [yt-dlp](https://github.com/yt-dlp/yt-dlp/releases/download/2026.11.23/yt-dlp) (zipapp, needs python3 ≥ 3.11 in container; the plugin runner uses 3.12) | `e8665ed64d6b9172773e812f891fb707a0cb1ee8d9ef4fc1acb00cabee4c5256` |

## QuickJS-NG — v0.17.0

Used as the JavaScript runtime for yt-dlp's EJS challenges when the host has no
deno. Enabled automatically by `tools/yt-dlp.py` via
`--js-runtimes quickjs:<path>`; a host deno (priority order: deno > quickjs)
always wins when installed. Linux builds are static-pie (no glibc dependency).

| File | Release asset | sha256 |
|---|---|---|
| `macosx/aarch64/qjs` | [qjs-darwin-arm64](https://github.com/quickjs-ng/quickjs/releases/download/v0.17.0/qjs-darwin-arm64) | `8be3ddfe3397d2e692e4e1e8972ee9d032a0a580505d2f8b4ea528cf1b651c11` |
| `macosx/x86_64/qjs` | [qjs-darwin-x86_64](https://github.com/quickjs-ng/quickjs/releases/download/v0.17.0/qjs-darwin-x86_64) | `9e5e101b4fd13cda3204222ca9f8be35412c41dcdef3745829633b7a67245412` |
| `linux/aarch64/qjs` | [qjs-linux-aarch64](https://github.com/quickjs-ng/quickjs/releases/download/v0.17.0/qjs-linux-aarch64) | `3372133484edf50a69f3c67903af41206d22a061e930e3cfb63269272ef56d2e` |
| `linux/x86_64/qjs` | [qjs-linux-x86_64](https://github.com/quickjs-ng/quickjs/releases/download/v0.17.0/qjs-linux-x86_64) | `0bfc02511a9f549c28b53880d988fc7cd5d361e90c5e8afdfcd7dc6774ceace5` |

## Updating

```bash
VER=2026.11.23  # yt-dlp tag
QJS=v0.17.0     # quickjs-ng tag
curl -sL -o binary/macosx/yt-dlp_macos "https://github.com/yt-dlp/yt-dlp/releases/download/${VER}/yt-dlp_macos"
curl -sL -o binary/linux/yt-dlp        "https://github.com/yt-dlp/yt-dlp/releases/download/${VER}/yt-dlp"
curl -sL -o binary/macosx/aarch64/qjs  "https://github.com/quickjs-ng/quickjs/releases/download/${QJS}/qjs-darwin-arm64"
curl -sL -o binary/macosx/x86_64/qjs   "https://github.com/quickjs-ng/quickjs/releases/download/${QJS}/qjs-darwin-x86_64"
curl -sL -o binary/linux/aarch64/qjs   "https://github.com/quickjs-ng/quickjs/releases/download/${QJS}/qjs-linux-aarch64"
curl -sL -o binary/linux/x86_64/qjs    "https://github.com/quickjs-ng/quickjs/releases/download/${QJS}/qjs-linux-x86_64"
chmod +x binary/macosx/yt-dlp_macos binary/linux/yt-dlp binary/macosx/*/qjs binary/linux/*/qjs
shasum -a 256 binary/macosx/yt-dlp_macos binary/linux/yt-dlp binary/macosx/*/qjs binary/linux/*/qjs
```

Keep the git executable bit set (`chmod +x` before committing) — the plugin also
repairs permissions at runtime. Verify with a PATH that has no deno:

```bash
env PATH=/usr/bin:/bin python3 tools/yt-dlp.py -o /tmp/check.mp4 "https://www.youtube.com/watch?v=jNQXAC9IVRw"
```
