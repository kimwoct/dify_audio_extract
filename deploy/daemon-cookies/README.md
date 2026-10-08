# Private server-side YouTube cookies (plugin 0.0.8)

The plugin uses `/etc/dify/yt-dlp/youtube-cookies.txt` when no explicit
`cookies_file` is supplied. `YT_DLP_COOKIES_FILE` can override this path in
the plugin process environment, mainly for local testing. The default path
requires no environment-variable forwarding from the plugin daemon.

This is one shared YouTube session for calls that use this fallback. The
file is outside the package and Git, but administrators and processes that
can read the daemon mount can access it. It is not isolated from other
plugins running with the same filesystem permissions.

## 1. Place the cookies on the Docker host once

On the Docker host, in the directory containing Dify's `docker-compose.yaml`,
create a private directory and copy your Netscape export into it:

```sh
mkdir -p ./secrets/yt-dlp
chmod 700 ./secrets/yt-dlp
install -m 600 /private/path/to/youtube-cookies.txt \
  ./secrets/yt-dlp/youtube-cookies.txt
```

`/private/path/to/youtube-cookies.txt` must exist on the Docker host. Transfer
it there privately first; this is not a Dify `/files/upload` operation. Do
not paste its contents into commands, logs, or chat. Set directory and file
ownership to the UID/GID of the Python plugin process if it is not the owner.
Do not make the file world-readable to resolve permission errors.

## 2. Mount the directory read-only

Merge the `plugin_daemon.volumes` entry in this folder's
`docker-compose.override.yaml` into the existing override beside Dify's
compose file. Preserve existing ffmpeg, deno, and other mounts. Mounting the
directory rather than a single file allows cookie replacement without a
container restart. Missing source directories fail rather than being
silently created by Compose.

After reviewing the merged configuration, apply it on the Docker host:

```sh
docker compose config --quiet
docker compose up -d plugin_daemon
docker compose exec plugin_daemon sh -c \
  'test -f /etc/dify/yt-dlp/youtube-cookies.txt && test -r /etc/dify/yt-dlp/youtube-cookies.txt'
```

The last command checks file presence/readability without printing cookies.
If the plugin process runs under a different UID, verify readability under
that UID as well. Container-root readability alone is insufficient.

## 3. Install the package and remove workflow file binding

Install `/tmp/yt-dlp-cookie-support-0.0.8.difypkg` using Dify's local package
installer. The package contains the fallback code, not the cookies.

In your workflow:

1. Clear the yt-dlp node's **YouTube Cookies File** binding.
2. Remove the `cookies` User Input field and its uploaded default, after
   checking that no other node consumes it.
3. Publish the updated workflow.

The optional tool file parameter is retained for existing workflows that
still need per-call cookies. Leave it unbound for this server-side setup.
Removing the workflow input prevents Dify from validating its old file
default before the plugin gets a chance to use the fallback.

Your existing API request can then omit `cookies`; no `/files/upload` call
is needed. Keep `video_url`, `lang_choice`, `output_type`, `query`, and
`user` as before. See `COOKIE_API.md` for an example.

## 4. Replace cookies when needed

On the Docker host, copy a fresh Netscape export into a temporary file in
the mounted directory, then replace the source atomically:

```sh
install -m 600 /private/path/to/fresh-youtube-cookies.txt \
  ./secrets/yt-dlp/youtube-cookies.next.txt
mv ./secrets/yt-dlp/youtube-cookies.next.txt \
  ./secrets/yt-dlp/youtube-cookies.txt
```

Preserve the plugin process ownership when replacing the file. Future
invocations read the new contents; in-progress invocations use their private
copy. No package rebuild is necessary.

### One-shot refresh from your own machine

`refresh-youtube-cookies.sh` (this folder) automates the whole replacement:
it mints a fresh Netscape export with `yt-dlp --cookies-from-browser` (or
imports an existing export with `--file`), validates the file without ever
printing contents, ships it over `scp`, swaps it atomically into
`./secrets/yt-dlp/`, removes staging copies on both machines, and verifies
reachability from inside the plugin daemon container. No restart, no
container rebuild.

```sh
./deploy/daemon-cookies/refresh-youtube-cookies.sh \
  -s user@dify-host -d /srv/dify/docker                      # mint from Safari
./deploy/daemon-cookies/refresh-youtube-cookies.sh \
  -s user@dify-host -d /srv/dify/docker \
  -b chrome                                                  # mint from Chrome
./deploy/daemon-cookies/refresh-youtube-cookies.sh \
  -s user@dify-host -d /srv/dify/docker \
  -f ~/Downloads/youtube-cookies.txt                         # existing export
./deploy/daemon-cookies/refresh-youtube-cookies.sh -h        # all options
```

The browser must be closed during the mint step (its cookie database is
locked while the browser runs; Safari may trigger a Keychain prompt).

Optional scheduling, replace weekly from cron:

```cron
23 4 * * 1 /path/to/dify/deploy/daemon-cookies/refresh-youtube-cookies.sh \
  -s user@dify-host -d /srv/dify/docker -y \
  >> /tmp/yt-cookie-refresh.log 2>&1
```

Cookie contents are never logged; the log holds only header lines, counts,
and install confirmations. A cron run still needs an unexpired browser
session on that machine; otherwise YouTube rejects the export and the
script fails before touching the server.

## 5. Manual refresh runbook (after the session expires)

YouTube invalidates the shared session without changing anything inside the
file, so expiry shows up as runtime errors, not as a stale-file flag. Run
this end to end when that happens. Section 4's `refresh-youtube-cookies.sh`
scripts Steps 1–4 in one command; this runbook is the manual equivalent.

### Step 0 — once: put the mount on a durable footing

Run on the Docker host, in the `docker/` directory of the Dify checkout (the
one containing `docker-compose.yaml`). First check which placement you are
on now:

```sh
docker compose exec -T plugin_daemon mount | grep '/etc/dify' \
  && echo "→ bind mount exists (good — skip to Step 1)" \
  || echo "→ no mount: file was docker-cp'd (ephemeral — do the setup below)"
```

If ephemeral, make it durable. Edit `docker-compose.override.yaml` and merge
this into the existing `plugin_daemon` service (keep ffmpeg, deno, and other
existing mounts):

```yaml
services:
  plugin_daemon:
    volumes:
      - type: bind
        source: ./secrets/yt-dlp
        target: /etc/dify/yt-dlp
        read_only: true
        bind:
          create_host_path: false
```

```sh
mkdir -p ./secrets/yt-dlp && chmod 700 ./secrets/yt-dlp
docker compose config --quiet              # must pass
docker compose up -d --no-deps plugin_daemon   # one-time recreate; active plugin tasks pause briefly
```

The mount targets the *directory*, not the file, so future refreshes swap
the file on the host and the container picks it up on the next plugin
invocation — no restart, ever. If you instead migrated to the
`./volumes/plugin_daemon/yt-dlp/` + `YT_DLP_COOKIES_FILE=/app/storage/yt-dlp/youtube-cookies.txt`
route, this runbook still applies — only swap the paths in Steps 3–4.

### Step 1 — every refresh: export fresh cookies on your local machine

Option A (browser extension): log into youtube.com in the browser, run
"Get cookies.txt LOCALLY" (Chrome) or "cookies.txt" (Firefox), and export
Netscape format to `~/Downloads/youtube-cookies-fresh.txt`.

Option B (yt-dlp mints it directly): quit the browser first (its cookie
database is locked while it runs), then:

```sh
yt-dlp --cookies-from-browser safari \
  --cookies ~/Downloads/youtube-cookies-fresh.txt \
  "https://www.youtube.com/watch?v=1Wz7lzmGfsk"
```

Then verify with structural checks only — never print the contents:

```sh
head -1 ~/Downloads/youtube-cookies-fresh.txt   # expect: "# Netscape HTTP Cookie File"
wc -l  ~/Downloads/youtube-cookies-fresh.txt    # expect: dozens/hundreds of rows
```

### Step 2 — every refresh: transfer privately to the docker host

```sh
scp ~/Downloads/youtube-cookies-fresh.txt <user>@<dify-host>:/tmp/youtube-cookies-fresh.txt
```

Never email, chat, or commit this file.

### Step 3 — every refresh: install atomically on the host

The mount is read-only for the container, so replace the file on the host
(`/tmp/youtube-cookies-fresh.txt` already arrived via Step 2):

```sh
cd /path/to/dify/docker
mkdir -p ./secrets/yt-dlp && chmod 700 ./secrets/yt-dlp
install -m 600 /tmp/youtube-cookies-fresh.txt \
  ./secrets/yt-dlp/.youtube-cookies.txt.new \
  && mv -f ./secrets/yt-dlp/.youtube-cookies.txt.new \
          ./secrets/yt-dlp/youtube-cookies.txt
rm /tmp/youtube-cookies-fresh.txt             # wipe the staging copy
rm ~/Downloads/youtube-cookies-fresh.txt      # on your machine: wipe the source too
```

The hidden staging name plus `mv` swap means a plugin reading mid-refresh
never sees a half-written file, and in-progress invocations keep their
private copy.

### Step 4 — verify (nothing content-sensitive printed)

From the host:

```sh
ls -l ./secrets/yt-dlp/                        # check mtime updated
docker compose exec plugin_daemon sh -c \
  'head -1 /etc/dify/yt-dlp/youtube-cookies.txt && test -s /etc/dify/yt-dlp/youtube-cookies.txt && echo "REACHABLE"'
```

### Step 5 — end-to-end proof

Re-run the workflow API request from section 3 (see `COOKIE_API.md`). A
succeeded response with questions in the outputs means the new session is
live; no container restart was needed at any point.

### How you'll know next time

The workflow API call starts failing with a yt-dlp bot-check error ("Sign in
to confirm you're not a bot" style) while every other input is unchanged.
That is the refresh signal — not a timer: formatting, expiry timestamps, and
file size stay valid after YouTube has already killed the session. A typical
exported session survives weeks to months of use.

## Diagnostics

- `Cookies: server-side (validated Netscape file)`: fallback was loaded.
- `Cookies: supplied (validated Netscape file)`: an explicit file took priority.
- `Cookies: not provided`: no explicit file or default server file was found.
- Invalid/empty/all-expired server files fail before yt-dlp launches.
- Rejected server cookies must be replaced; valid formatting and expiry
  timestamps do not prove YouTube still accepts the session.

Local tests do not establish a successful deployment. After installation,
mounting, and publication, run the actual workflow API end to end.
