import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("yt_dlp_tool", ROOT / "tools" / "yt-dlp.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

COOKIE = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tprivate-test-value\n"


class CookieTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.server_file = Path(directory.name) / "youtube-cookies.txt"
        server_patch = patch.object(tool, "_SERVER_COOKIES_FILE", self.server_file)
        server_patch.start()
        self.addCleanup(server_patch.stop)

    def test_optional_cookie_file(self):
        for value in (None, ""):
            with self.subTest(value=value):
                self.assertEqual(tool._cookie_args(value), ([], None, "not provided"))

    def test_server_file_is_used_without_upload(self):
        self.server_file.write_bytes(COOKIE)
        for value in (None, ""):
            with self.subTest(value=value):
                args, staging, source = tool._cookie_args(value)
                try:
                    self.assertEqual(source, "server-side")
                    staged = Path(args[1])
                    self.assertNotEqual(staged, self.server_file)
                    self.assertEqual(staged.read_bytes(), COOKIE)
                    self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
                    staged.write_bytes(b"updated by yt-dlp")
                    self.assertEqual(self.server_file.read_bytes(), COOKIE)
                finally:
                    staging.cleanup()
                self.assertFalse(staged.exists())

    def test_explicit_upload_wins_over_server_file(self):
        self.server_file.write_bytes(b"invalid server cookies")
        args, staging, source = tool._cookie_args(SimpleNamespace(blob=COOKIE))
        try:
            self.assertEqual(source, "supplied")
            self.assertEqual(Path(args[1]).read_bytes(), COOKIE)
        finally:
            staging.cleanup()

    def test_explicit_local_file_wins_over_server_file(self):
        self.server_file.write_bytes(b"invalid server cookies")
        local_file = self.server_file.parent / "explicit-cookies.txt"
        local_file.write_bytes(COOKIE)
        args, staging, source = tool._cookie_args(local_file)
        try:
            self.assertEqual(source, "supplied")
            self.assertEqual(Path(args[1]).read_bytes(), COOKIE)
        finally:
            staging.cleanup()

    def test_invalid_upload_does_not_fall_back_to_server_credentials(self):
        self.server_file.write_bytes(COOKIE)
        with self.assertRaises(ValueError):
            tool._cookie_args(SimpleNamespace(blob=b"{}"))

    def test_invalid_server_file_is_rejected(self):
        for blob in (b"", b"{}", COOKIE.replace(b"\t0\t", b"\t1\t")):
            with self.subTest(blob_kind=len(blob)):
                self.server_file.write_bytes(blob)
                with self.assertRaises(ValueError):
                    tool._cookie_args(None)

    def test_unreadable_server_file_is_not_silently_ignored(self):
        self.server_file.write_bytes(COOKIE)
        with patch.object(Path, "read_bytes", side_effect=PermissionError("permission denied")):
            with self.assertRaises(PermissionError):
                tool._cookie_args(None)

    def test_upload_is_private_and_removed(self):
        args, staging, _ = tool._cookie_args(SimpleNamespace(blob=COOKIE))
        path = Path(args[1])
        try:
            self.assertEqual(args[0], "--cookies")
            self.assertEqual(path.read_bytes(), COOKIE)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        finally:
            staging.cleanup()
        self.assertFalse(path.exists())

    def test_local_file_is_copied_not_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "cookies.txt"
            original.write_bytes(COOKIE)
            args, staging, _ = tool._cookie_args(original)
            try:
                self.assertNotEqual(Path(args[1]), original)
                Path(args[1]).write_bytes(b"yt-dlp rewrote the cookie jar")
                self.assertEqual(original.read_bytes(), COOKIE)
            finally:
                staging.cleanup()

    def test_bom_and_windows_newlines_are_normalized(self):
        blob = b"\xef\xbb\xbf" + COOKIE.replace(b"\n", b"\r\n")
        args, staging, _ = tool._cookie_args(SimpleNamespace(blob=blob))
        try:
            self.assertEqual(Path(args[1]).read_bytes(), COOKIE)
        finally:
            staging.cleanup()

    def test_http_only_and_empty_session_expiry(self):
        blob = COOKIE.replace(b".youtube.com", b"#HttpOnly_.youtube.com").replace(b"\t0\t", b"\t\t")
        args, staging, _ = tool._cookie_args(SimpleNamespace(blob=blob))
        try:
            self.assertEqual(Path(args[1]).read_bytes(), blob)
        finally:
            staging.cleanup()

    def test_alternative_header_and_empty_value(self):
        blob = COOKIE.replace(b"# Netscape HTTP Cookie File", b"# HTTP Cookie File").replace(b"private-test-value", b"")
        args, staging, _ = tool._cookie_args(SimpleNamespace(blob=blob))
        try:
            self.assertEqual(Path(args[1]).read_bytes(), blob)
        finally:
            staging.cleanup()

    def test_future_expiry_is_accepted(self):
        args, staging, _ = tool._cookie_args(SimpleNamespace(blob=COOKIE.replace(b"\t0\t", b"\t4102444800\t")))
        staging.cleanup()

    def test_expired_cookies_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "have expired"):
            tool._cookie_args(SimpleNamespace(blob=COOKIE.replace(b"\t0\t", b"\t1\t")))

    def test_invalid_inputs_do_not_leak_values(self):
        invalid_blobs = (
            b"",
            b'{"SID": "private-test-value"}',
            b"# Netscape HTTP Cookie File\n",
            COOKIE.replace(b"\t", b" "),
            COOKIE.replace(b"\t0\t", b"\tbad-expiry\t"),
            COOKIE.replace(b"\tTRUE\t/", b"\tFALSE\t/"),
            COOKIE.replace(b"\tTRUE\t0", b"\tINVALID\t0"),
            COOKIE + b"\xff",
        )
        for blob in invalid_blobs:
            with self.subTest(blob_kind=invalid_blobs.index(blob)):
                with self.assertRaises(ValueError) as error:
                    tool._cookie_args(SimpleNamespace(blob=blob))
                self.assertNotIn("private-test-value", str(error.exception))

    def test_raw_api_file_metadata_is_not_a_file_blob(self):
        with self.assertRaisesRegex(ValueError, "uploaded Netscape cookie file"):
            tool._cookie_args({"transfer_method": "local_file", "upload_file_id": "example"})

    def test_missing_local_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                tool._cookie_args(Path(directory) / "missing.txt")

    def test_staging_failure_cleans_up(self):
        real_temporary_directory = tempfile.TemporaryDirectory
        directories = []

        def make_staging(*args, **kwargs):
            directory = real_temporary_directory(*args, **kwargs)
            directories.append(Path(directory.name))
            return directory

        with patch.object(tool.tempfile, "TemporaryDirectory", side_effect=make_staging):
            with patch.object(Path, "chmod", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    tool._cookie_args(SimpleNamespace(blob=COOKIE))
        self.assertTrue(directories)
        self.assertTrue(all(not directory.exists() for directory in directories))


class RunnerCookieTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.server_file = Path(directory.name) / "youtube-cookies.txt"
        server_patch = patch.object(tool, "_SERVER_COOKIES_FILE", self.server_file)
        server_patch.start()
        self.addCleanup(server_patch.stop)

    def run_with_mock_process(self, cookies, *, fail=False, launch_error=False):
        staged_paths = []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "audio.mp3"

            def run(args, **kwargs):
                if "--cookies" in args:
                    staged = Path(args[args.index("--cookies") + 1])
                    staged_paths.append(staged)
                    self.assertEqual(staged.read_bytes(), COOKIE)
                self.assertEqual(args[-2:], ["--", "https://www.youtube.com/watch?v=1Wz7lzmGfsk"])
                if launch_error:
                    raise OSError("launch failed")
                if not fail:
                    output.write_bytes(b"mock audio")
                return subprocess.CompletedProcess(args, int(fail), "", "Sign in to confirm you're not a bot" if fail else "")

            with patch.object(tool, "_strip_quarantine"), patch.object(tool, "_js_runtime_args", return_value=([], "test runtime")):
                with patch.object(tool.subprocess, "run", side_effect=run):
                    if fail or launch_error:
                        with self.assertRaises((RuntimeError, OSError)) as error:
                            tool._run_yt_dlp("https://www.youtube.com/watch?v=1Wz7lzmGfsk", str(output), extract_audio=True, cookies_file=cookies)
                        message = str(error.exception)
                        self.assertNotIn("private-test-value", message)
                    else:
                        result = tool._run_yt_dlp("https://www.youtube.com/watch?v=1Wz7lzmGfsk", str(output), extract_audio=True, cookies_file=cookies)
                        self.assertEqual(result.read_bytes(), b"mock audio")
                        message = ""
        self.assertTrue(all(not path.exists() for path in staged_paths))
        return message, staged_paths

    def test_upload_is_forwarded_and_cleaned_after_success(self):
        _, paths = self.run_with_mock_process(SimpleNamespace(blob=COOKIE))
        self.assertEqual(len(paths), 1)

    def test_server_file_is_forwarded_without_cookie_parameter(self):
        self.server_file.write_bytes(COOKIE)
        _, paths = self.run_with_mock_process(None)
        self.assertEqual(len(paths), 1)
        self.assertEqual(self.server_file.read_bytes(), COOKIE)

    def test_server_cookie_rejection_is_identified_and_cleaned(self):
        self.server_file.write_bytes(COOKIE)
        message, paths = self.run_with_mock_process(None, fail=True)
        self.assertIn("Cookies: server-side", message)
        self.assertIn("replace the private server-side cookie file", message)
        self.assertEqual(len(paths), 1)

    def test_server_cookie_launch_failure_is_cleaned(self):
        self.server_file.write_bytes(COOKIE)
        _, paths = self.run_with_mock_process(None, launch_error=True)
        self.assertEqual(len(paths), 1)

    def test_supplied_cookie_failure_is_distinguished_and_cleaned(self):
        message, paths = self.run_with_mock_process(SimpleNamespace(blob=COOKIE), fail=True)
        self.assertIn("Cookies: supplied", message)
        self.assertIn("YouTube rejected the supplied cookies", message)
        self.assertEqual(len(paths), 1)

    def test_missing_cookie_failure_explains_binding(self):
        message, paths = self.run_with_mock_process(None, fail=True)
        self.assertIn("Cookies: not provided", message)
        self.assertIn("no server-side cookie file was found", message)
        self.assertEqual(paths, [])

    def test_launch_failure_cleans_cookie_file(self):
        _, paths = self.run_with_mock_process(SimpleNamespace(blob=COOKIE), launch_error=True)
        self.assertEqual(len(paths), 1)

    def test_invalid_cookie_never_reaches_yt_dlp(self):
        with patch.object(tool, "_strip_quarantine"), patch.object(tool, "_js_runtime_args", return_value=([], "test runtime")):
            with patch.object(tool.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    tool._run_yt_dlp("https://www.youtube.com/watch?v=1Wz7lzmGfsk", cookies_file=SimpleNamespace(blob=b"{}"))
                run.assert_not_called()

    def test_tool_invoke_without_cookie_parameter_uses_fallback(self):
        self.server_file.write_bytes(COOKIE)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "audio.mp3"

            def run(args, **kwargs):
                self.assertIn("--cookies", args)
                staged = Path(args[args.index("--cookies") + 1])
                self.assertEqual(staged.read_bytes(), COOKIE)
                Path(args[args.index("--output") + 1]).write_bytes(b"test audio")
                return subprocess.CompletedProcess(args, 0, "", "")

            original_runner = tool._run_yt_dlp

            def run_tool(**kwargs):
                self.assertIsNone(kwargs["cookies_file"])
                kwargs["output"] = str(output)
                return original_runner(**kwargs)

            fake_tool = SimpleNamespace(create_blob_message=lambda **kwargs: kwargs)
            with patch.object(tool, "_strip_quarantine"), patch.object(tool, "_js_runtime_args", return_value=([], "test runtime")):
                with patch.object(tool, "_run_yt_dlp", side_effect=run_tool), patch.object(tool.subprocess, "run", side_effect=run):
                    messages = list(tool.YtDlpTool._invoke(fake_tool, {
                        "url": "https://www.youtube.com/watch?v=1Wz7lzmGfsk",
                        "extract_audio": "True",
                        "audio_format": "mp3",
                        "audio_quality": "5",
                    }))
            self.assertEqual(messages[0]["blob"], b"test audio")
            self.assertEqual(messages[0]["meta"]["mime_type"], "audio/mpeg")


if __name__ == "__main__":
    unittest.main()
