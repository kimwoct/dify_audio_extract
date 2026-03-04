from collections.abc import Generator
import argparse
import os
from pathlib import Path
import platform
import signal
import shutil
import subprocess
import tempfile
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


def _run_yt_dlp(
    url: str,
    output: str | None = None,
    *,
    extract_audio: bool = False,
    audio_format: str = "mp3",
    audio_quality: int = 5,
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

    if current_platform == "darwin":
        subprocess.run(
            ["xattr", "-d", "com.apple.quarantine", str(binary_path)],
            cwd=root_dir,
            capture_output=True,
            text=True,
            check=False,
        )

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

        raise RuntimeError(
            f"yt-dlp failed with exit code {result.returncode}{signal_text}.{extra_hint} "
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
    args = parser.parse_args()

    output_path = _run_yt_dlp(
        url=args.url,
        output=args.output,
        extract_audio=args.extract_audio,
        audio_format=args.audio_format,
        audio_quality=args.audio_quality,
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
