# Cookies for workflow API calls (plugin 0.0.8)

## Server-side mode: no per-call upload

When `cookies_file` is omitted, the plugin reads the private server file
`/etc/dify/yt-dlp/youtube-cookies.txt`. It validates and stages a temporary
copy, then deletes that copy after yt-dlp exits. An explicit file takes
precedence; an invalid explicit file does not silently switch sessions.

Follow `deploy/daemon-cookies/README.md` to place the file on the Docker host
and mount its directory read-only. Install plugin 0.0.8, clear the tool's
cookie binding, remove the workflow's `cookies` input/default, and publish.
These workflow changes are necessary to avoid file-mapping validation
before the plugin runs. Existing per-call file support is preserved.

Then call the workflow directly, without `/files/upload` or a `cookies` key:

```sh
curl --fail-with-body --request POST "$DIFY_BASE_URL/workflows/run" \
  --header "Authorization: Bearer $DIFY_API_KEY" \
  --header 'Content-Type: application/json' \
  --data '{
    "inputs": {
      "video_url": "https://www.youtube.com/watch?v=1Wz7lzmGfsk",
      "lang_choice": "yue",
      "output_type": "Subtitle",
      "query": "Review the timestamped meeting transcript and summarize key takeaways and action items with named owners."
    },
    "response_mode": "blocking",
    "user": "abc-123"
  }'
```

Set `DIFY_BASE_URL` to your API base including `/v1` and `DIFY_API_KEY` to
your app's key. Keep your original query text if you need the same prompt.
All calls using the fallback share this server-side YouTube session.

For a local fallback check (no `--cookies` argument):

```sh
YT_DLP_COOKIES_FILE="$HOME/Downloads/yt-dlp/youtube-cookies.txt" \
  python3 tools/yt-dlp.py 'https://www.youtube.com/watch?v=1Wz7lzmGfsk' \
  --extract-audio --audio-format mp3 --audio-quality 5 \
  --output /tmp/yt-dlp-server-cookie-check.mp3
```

## Optional per-call upload mode

Use an uploaded `cookies.txt` file in Netscape format. JSON is the transport
for the workflow request, not the cookie format. Do not embed cookies in the
plugin package. In this optional mode, callers supply their own session file.
Short text, a browser `Cookie:` header, and JSON cookie exports are not supported.

## Configure the workflow

Keep the existing User Input variables:

- `video_url`: Text.
- `cookies`: Single File, accepting `.txt` files.

In the yt-dlp node, bind URL to `User Input.video_url` and **YouTube Cookies
File** to `User Input.cookies`. Keep Extract Audio set to `True`, Audio Format
to `mp3`, and Audio Quality to `5` for the audio request in this example.

The request containing only `url`, `extract_audio`, `audio_format`, and
`audio_quality` does not provide cookies. Binding the tool's file parameter
does not upload a file for subsequent API calls; each workflow API request
must also supply the input file unless the server-side fallback above is used.

## 1. Upload the file

Set `DIFY_BASE_URL` to the server's API base URL, including `/v1`, and set
`DIFY_API_KEY` to this workflow app's API key. Use the same `user` value for
both requests. Upload the file from the API caller's machine, not a path
inside the plugin container:

```sh
curl --fail-with-body --request POST "$DIFY_BASE_URL/files/upload" \
  --header "Authorization: Bearer $DIFY_API_KEY" \
  --form "file=@$HOME/Downloads/yt-dlp/youtube-cookies.txt;type=text/plain" \
  --form 'user=yt-dlp-api-user'
```

Copy the `id` field from the successful upload response.

## 2. Run the published workflow

Replace `UPLOAD_ID_FROM_STEP_1` with that ID. The `inputs` keys must match
your workflow's User Input names; these are `video_url` and `cookies` in the
configuration above. The single-file input is an object, not an array:

```sh
curl --fail-with-body --request POST "$DIFY_BASE_URL/workflows/run" \
  --header "Authorization: Bearer $DIFY_API_KEY" \
  --header 'Content-Type: application/json' \
  --data '{
    "inputs": {
      "video_url": "https://www.youtube.com/watch?v=1Wz7lzmGfsk",
      "cookies": {
        "type": "document",
        "transfer_method": "local_file",
        "upload_file_id": "UPLOAD_ID_FROM_STEP_1"
      }
    },
    "response_mode": "blocking",
    "user": "yt-dlp-api-user"
  }'
```

Dify resolves this input into a file object before invoking the plugin.
Do not send this upload descriptor directly to `_run_yt_dlp`, or use a local
filesystem path as a workflow file input.

## Cookie validation and cleanup

- Cookie files have a Netscape header and valid tab-separated rows.
- UTF-8 BOMs and Windows line endings are normalized.
- Empty files, malformed exports, and files whose cookies are all expired
  fail before yt-dlp launches, without printing cookie values.
- Uploaded and local files are copied into a private temporary directory.
  The copy is removed on success or failure; yt-dlp cannot overwrite the
  original local file.
- Download failures state `Cookies: not provided` or
  `Cookies: supplied (validated Netscape file)`, or
  `Cookies: server-side (validated Netscape file)`. A syntactically valid file
  can still contain cookies that YouTube has rotated or revoked.

If YouTube rejects supplied cookies, export a fresh private-session file:
sign into YouTube in a new private window, visit YouTube's `robots.txt` in
the same tab, export YouTube cookies in Netscape format, and close that
window. Upload the new file and use the new upload ID. Expiry timestamps
alone do not establish whether YouTube still accepts the session.

If extraction succeeds but mp3 conversion reports missing ffmpeg/ffprobe,
those programs must be installed in the Dify plugin daemon; see
`deploy/daemon-ffmpeg/README.md`.

## Local check

```sh
python3 -m unittest discover -s tests -v
python3 tools/yt-dlp.py 'https://www.youtube.com/watch?v=1Wz7lzmGfsk' \
  --cookies "$HOME/Downloads/yt-dlp/youtube-cookies.txt" \
  --extract-audio --audio-format mp3 --audio-quality 5 \
  --output /tmp/yt-dlp-cookie-check.mp3
```

This local check does not verify the Dify daemon's network, file binding,
or media dependencies. Verify a workflow API request on the actual server
after installing the updated package.
