from __future__ import annotations

from collections.abc import Generator
import argparse
import os
from pathlib import Path
import platform
import signal
import shutil
import subprocess
import tempfile
import time
from typing import Any
import uuid

try:
    from dify_plugin import Tool
    from dify_plugin.entities.tool import ToolInvokeMessage
except ModuleNotFoundError:  # pragma: no cover - fallback for local CLI execution
    Tool = object  # type: ignore[assignment]
    ToolInvokeMessage = Any  # type: ignore[misc,assignment]


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


_ARCH_ALIASES = {
    "arm64": "aarch64",
    "aarch64": "aarch64",
    "x86_64": "x86_64",
    "amd64": "x86_64",
}

_SERVER_COOKIES_FILE = Path(
    os.environ.get("YT_DLP_COOKIES_FILE", "/etc/dify/yt-dlp/youtube-cookies.txt")
)


def _strip_quarantine(path: Path) -> None:
    if platform.system().lower() != "darwin":
        return
    subprocess.run(
        ["xattr", "-d", "com.apple.quarantine", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def _js_runtime_args(root_dir: Path, current_platform: str) -> tuple[list[str], str]:
    """Enable the bundled QuickJS-NG binary as a yt-dlp JavaScript runtime.

    deno stays enabled by default with higher priority, so a deno installed on
    the host wins; the bundled quickjs is the fallback (e.g. inside the plugin
    daemon container, where no runtime exists). Returns the yt-dlp args and a
    human-readable status that is surfaced in error messages.
    """
    arch = _ARCH_ALIASES.get(platform.machine().lower())
    if not arch:
        return [], f"unsupported architecture {platform.machine()!r}, no bundled runtime"
    platform_dir = "macosx" if current_platform == "darwin" else "linux"
    qjs_path = root_dir / "binary" / platform_dir / arch / "qjs"
    if not qjs_path.exists():
        return [], f"bundled quickjs not found at {qjs_path}"
    if not os.access(qjs_path, os.X_OK):
        try:
            qjs_path.chmod(qjs_path.stat().st_mode | 0o111)
        except OSError as e:
            return [], f"quickjs at {qjs_path} is not executable (chmod failed: {e})"
    _strip_quarantine(qjs_path)
    return ["--js-runtimes", f"quickjs:{qjs_path}"], f"enabled bundled quickjs at {qjs_path}"


def _cookie_args(
    cookies_file: Any,
) -> tuple[list[str], tempfile.TemporaryDirectory | None, str]:
    """Stage cookies privately, preferring an explicit file over the server file."""
    cookie_source = "supplied"
    if cookies_file is None or cookies_file == "":
        if not _SERVER_COOKIES_FILE.is_file():
            return [], None, "not provided"
        cookie_path = _SERVER_COOKIES_FILE
        cookie_source = "server-side"
        cookie_blob = cookie_path.read_bytes()
    elif isinstance(cookies_file, (str, Path)):
        cookie_path = Path(cookies_file).expanduser()
        if not cookie_path.is_file():
            raise FileNotFoundError(f"Cookie file not found: {cookie_path}")
        cookie_blob = cookie_path.read_bytes()
    else:
        cookie_blob = getattr(cookies_file, "blob", None)
    if not isinstance(cookie_blob, (bytes, bytearray)):
        raise ValueError("Parameter 'cookies_file' must be an uploaded Netscape cookie file, not JSON or cookie text.")
    if not cookie_blob:
        raise ValueError("Parameter 'cookies_file' is empty.")

    try:
        cookie_text = cookie_blob.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise ValueError("Parameter 'cookies_file' must be a UTF-8 Netscape cookie file.") from None
    lines = cookie_text.splitlines()
    if not lines or lines[0] not in {"# Netscape HTTP Cookie File", "# HTTP Cookie File"}:
        raise ValueError("Parameter 'cookies_file' must have a Netscape HTTP Cookie File header; JSON is not supported.")

    cookie_count = 0
    usable_count = 0
    now = time.time()
    for line_number, line in enumerate(lines[1:], start=2):
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.lstrip().startswith(("#", "$")):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            raise ValueError(f"Invalid Netscape cookie row at line {line_number}: expected 7 tab-separated fields.")
        domain, include_subdomains, path, secure, expires, name, value = fields
        if (
            not domain
            or include_subdomains not in {"TRUE", "FALSE"}
            or (include_subdomains == "TRUE") != domain.startswith(".")
            or secure not in {"TRUE", "FALSE"}
            or (expires and not expires.isascii())
            or (expires and not expires.isdecimal())
        ):
            raise ValueError(f"Invalid Netscape cookie fields at line {line_number}; export a fresh cookies.txt file.")
        cookie_count += 1
        if not expires or int(expires) == 0 or int(expires) > now:
            usable_count += 1
    if not cookie_count:
        raise ValueError("Parameter 'cookies_file' contains no cookies.")
    if not usable_count:
        raise ValueError("All cookies in 'cookies_file' have expired; export and upload fresh YouTube cookies.")

    cookie_staging = tempfile.TemporaryDirectory(prefix="yt_dlp_cookies_")
    cookie_path = Path(cookie_staging.name) / "cookies.txt"
    try:
        with cookie_path.open("xb") as cookie_stream:
            cookie_path.chmod(0o600)
            cookie_stream.write(cookie_text.encode("utf-8"))
    except BaseException:
        cookie_staging.cleanup()
        raise
    return ["--cookies", str(cookie_path)], cookie_staging, cookie_source


def _run_yt_dlp(
    url: str,
    output: str | None = None,
    *,
    extract_audio: bool = False,
    audio_format: str = "mp3",
    audio_quality: int = 5,
    cookies_file: Any = None,
) -> Path:
    cleaned_url = str(url).strip()
    if not cleaned_url:
        raise ValueError("Parameter 'url' is required.")

    cleaned_audio_format = str(audio_format).strip().lower() or "mp3"
    parsed_audio_quality = int(audio_quality)
    if parsed_audio_quality < 0 or parsed_audio_quality > 10:
        raise ValueError("Parameter 'audio_quality' must be in range 0-10.")

    root_dir = Path(__file__).resolve().parent.parent
    current_platform = platform.system().lower()
    if current_platform == "darwin":
        binary_path = root_dir / "binary" / "macosx" / "yt-dlp_macos"
    elif "linux" in current_platform:
        binary_path = root_dir / "binary" / "linux" / "yt-dlp"
    else:
        raise RuntimeError(f"Unsupported platform: {platform.system()}")

    downloads_dir = root_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    default_extension = "mp3" if extract_audio else "mp4"
    if output:
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = root_dir / output_path
    else:
        output_path = downloads_dir / f"{uuid.uuid4()}.{default_extension}"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not binary_path.exists():
        raise FileNotFoundError(f"yt-dlp binary not found: {binary_path}")

    if not os.access(binary_path, os.X_OK):
        try:
            binary_path.chmod(binary_path.stat().st_mode | 0o111)
        except Exception as e:
            raise PermissionError(f"yt-dlp binary is not executable and chmod failed: {binary_path}") from e

    if not os.access(binary_path, os.X_OK):
        raise PermissionError(f"yt-dlp binary is not executable: {binary_path}")

    _strip_quarantine(binary_path)

    qjs_staging: tempfile.TemporaryDirectory | None = None
    cookie_staging: tempfile.TemporaryDirectory | None = None
    try:
        js_args, js_diag = _js_runtime_args(root_dir, current_platform)
        if current_platform != "darwin" and js_args:
            # Plugin-daemon storage volumes are frequently mounted noexec, where the
            # exec bit alone is not enough — yt-dlp would silently mark the runtime
            # unavailable. Stage a copy where execution is allowed, mirroring the
            # yt-dlp binary fallback below.
            qjs_staging = tempfile.TemporaryDirectory(prefix="yt_dlp_qjs_")
            staged = Path(qjs_staging.name) / "qjs"
            shutil.copy2(Path(js_args[1].split(":", 1)[1]), staged)
            staged.chmod(0o755)
            js_args = ["--js-runtimes", f"quickjs:{staged}"]
            js_diag = f"enabled quickjs staged at {staged}"

        cookie_args, cookie_staging, cookie_source = _cookie_args(cookies_file)

        base_args = ["--output", str(output_path)]
        if extract_audio:
            base_args.extend(
                [
                    "--extract-audio",
                    "--audio-format",
                    cleaned_audio_format,
                    "--audio-quality",
                    str(parsed_audio_quality),
                ]
            )
        else:
            # Keep video payloads bounded for the Dify file contract (PR #1).
            base_args.extend(
                [
                    "--format",
                    "bv*[height<=360][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=360][ext=mp4]+ba[ext=m4a]/b[height<=360][ext=mp4]/b[height<=360]/b",
                    "--merge-output-format",
                    "mp4",
                ]
            )
        base_args.extend(cookie_args)
        base_args.extend(js_args)
        base_args.extend(["--", cleaned_url])

        try:
            result = subprocess.run([str(binary_path), *base_args], capture_output=True, text=True, cwd=root_dir)
        except PermissionError as e:
            if e.errno != 13:
                raise

            with tempfile.TemporaryDirectory(prefix="yt_dlp_exec_") as temp_dir:
                fallback_binary = Path(temp_dir) / binary_path.name
                shutil.copy2(binary_path, fallback_binary)
                fallback_binary.chmod(0o755)
                result = subprocess.run([str(fallback_binary), *base_args], capture_output=True, text=True, cwd=root_dir)
    finally:
        if qjs_staging is not None:
            qjs_staging.cleanup()
        if cookie_staging is not None:
            cookie_staging.cleanup()

    if result.returncode != 0:
        stdout_tail = (result.stdout or "").strip()[-1000:]
        stderr_tail = (result.stderr or "").strip()[-1000:]

        signal_text = ""
        if result.returncode < 0:
            sig_num = -result.returncode
            try:
                signal_text = f" (signal={signal.Signals(sig_num).name})"
            except ValueError:
                signal_text = f" (signal={sig_num})"

        extra_hint = ""
        if current_platform == "darwin" and result.returncode == -9:
            extra_hint = " On macOS this may be caused by Gatekeeper/quarantine."

        cookie_diag = {
            "server-side": "server-side (validated Netscape file)",
            "supplied": "supplied (validated Netscape file)",
            "not provided": "not provided",
        }[cookie_source]
        if "Sign in to confirm" in (result.stderr or ""):
            if cookie_source == "server-side":
                extra_hint += " YouTube rejected the server-side cookies; replace the private server-side cookie file."
            elif cookie_args:
                extra_hint += " YouTube rejected the supplied cookies; export and upload fresh cookies. Valid file format does not guarantee a valid session."
            else:
                extra_hint += " No cookies were supplied and no server-side cookie file was found."

        raise RuntimeError(
            f"yt-dlp failed with exit code {result.returncode}{signal_text}.{extra_hint} "
            f"JS runtime: {js_diag}. "
            f"Cookies: {cookie_diag}. "
            f"stdout_tail: {stdout_tail or '(empty)'}; stderr_tail: {stderr_tail or '(empty)'}"
        )

    if not output_path.exists():
        candidates = sorted(
            output_path.parent.glob(f"{output_path.name}*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            candidates[0].replace(output_path)

    if not output_path.exists():
        raise FileNotFoundError(f"Download completed but output file was not found: {output_path}")

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bundled yt-dlp binary and save video file")
    parser.add_argument("url", help="Video URL to download")
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output file path. Defaults to ./downloads/<uuid>.mp4 or .mp3 when --extract-audio is used",
    )
    parser.add_argument(
        "--extract-audio",
        action="store_true",
        help="Extract audio only.",
    )
    parser.add_argument(
        "--audio-format",
        default="mp3",
        help="Audio format when --extract-audio is used. Default: mp3",
    )
    parser.add_argument(
        "--audio-quality",
        type=int,
        default=5,
        help="Audio quality 0-10 when --extract-audio is used. Default: 5",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        help="Path to a user-provided Netscape cookie file.",
    )
    args = parser.parse_args()

    output_path = _run_yt_dlp(
        url=args.url,
        output=args.output,
        extract_audio=args.extract_audio,
        audio_format=args.audio_format,
        audio_quality=args.audio_quality,
        cookies_file=args.cookies,
    )
    print(str(output_path))
    return 0


class YtDlpTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        is_audio = _to_bool(tool_parameters.get("extract_audio", False))
        output_path = _run_yt_dlp(
            url=str(tool_parameters.get("url", "")),
            output=None,
            extract_audio=is_audio,
            audio_format=str(tool_parameters.get("audio_format", "mp3") or "mp3"),
            audio_quality=int(tool_parameters.get("audio_quality", 5)),
            cookies_file=tool_parameters.get("cookies_file"),
        )

        file_bytes = output_path.read_bytes()
        yield self.create_blob_message(
            blob=file_bytes,
            meta={
                "mime_type": "audio/mpeg" if is_audio else "video/mp4",
                "filename": output_path.name,
                "file_name": output_path.name,
            },
        )


if __name__ == "__main__":
    raise SystemExit(main())
