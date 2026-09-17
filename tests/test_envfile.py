"""scripts/envfile.py - the one .env loader every entry point shares."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import envfile  # noqa: E402


class EnvFileTest(unittest.TestCase):

    def write(self, text):
        path = Path(tempfile.mkdtemp(prefix="cm-env-")) / ".env"
        path.write_text(text)
        return path

    def test_parses_keys_comments_quotes_and_export_prefix(self):
        path = self.write(
            "# comment\n\nLLM_PROVIDER=gemini\nexport COMFYUI_URL='http://pc:8188'\n"
            'GEMINI_API_KEY="k=with=equals"\nMALFORMED LINE\n=novalue\n')
        self.assertEqual(envfile.parse_env_file(path), {
            "LLM_PROVIDER": "gemini",
            "COMFYUI_URL": "http://pc:8188",
            "GEMINI_API_KEY": "k=with=equals",
        })

    def test_missing_file_is_empty_not_an_error(self):
        self.assertEqual(envfile.parse_env_file("/nonexistent/.env"), {})
        self.assertEqual(envfile.load_env_file("/nonexistent/.env", environ={}), [])

    def test_never_overrides_the_real_environment(self):
        path = self.write("LLM_PROVIDER=gemini\nNEW_KEY=from-file\n")
        environ = {"LLM_PROVIDER": "anthropic"}
        added = envfile.load_env_file(path, environ=environ)
        self.assertEqual(added, ["NEW_KEY"])
        self.assertEqual(environ, {"LLM_PROVIDER": "anthropic", "NEW_KEY": "from-file"})

    def test_defaults_to_the_repository_env_and_os_environ(self):
        self.assertEqual(envfile.DEFAULT_PATH, ROOT / ".env")
        self.assertIs(envfile.load_env_file.__defaults__[1], None)
        self.assertNotIn("CM_ENVFILE_PROBE", os.environ)


if __name__ == "__main__":
    unittest.main()
