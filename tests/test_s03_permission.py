import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "s03_permission" / "code.py"


def load_s03_module(temp_cwd: Path):
    fake_anthropic = types.ModuleType("anthropic")

    class FakeAnthropic:
        def __init__(self, *args, **kwargs):
            self.messages = types.SimpleNamespace(create=None)

    fake_dotenv = types.ModuleType("dotenv")
    setattr(fake_anthropic, "Anthropic", FakeAnthropic)
    setattr(fake_dotenv, "load_dotenv", lambda override=True: None)

    previous_modules = {
        "anthropic": sys.modules.get("anthropic"),
        "dotenv": sys.modules.get("dotenv"),
    }
    previous_cwd = Path.cwd()
    previous_model_id = os.environ.get("MODEL_ID")

    spec = importlib.util.spec_from_file_location("s03_permission_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)

    sys.modules["anthropic"] = fake_anthropic
    sys.modules["dotenv"] = fake_dotenv
    try:
        os.chdir(temp_cwd)
        os.environ["MODEL_ID"] = "test-model"
        spec.loader.exec_module(module)
        return module
    finally:
        os.chdir(previous_cwd)
        if previous_model_id is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = previous_model_id
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class S03PermissionTests(unittest.TestCase):
    def test_windows_delete_commands_match_gate_two(self):
        commands = [
            "del test.txt",
            "DEL /Q test.txt",
            "erase test.txt",
            "rd /s /q build",
            "rmdir build",
            "powershell -Command Remove-Item test.txt",
        ]

        with tempfile.TemporaryDirectory() as tmp:
            module = load_s03_module(Path(tmp))

            for command in commands:
                with self.subTest(command=command):
                    self.assertEqual(
                        module.check_rules("bash", {"command": command}),
                        "Potentially destructive command",
                    )

    def test_windows_delete_command_reaches_user_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s03_module(Path(tmp))
            block = types.SimpleNamespace(
                name="bash",
                input={"command": "del test.txt"},
            )

            with patch.object(module, "ask_user", return_value="deny") as ask_user:
                self.assertFalse(module.check_permission(block))

            ask_user.assert_called_once_with(
                "bash",
                {"command": "del test.txt"},
                "Potentially destructive command",
            )

    def test_read_only_windows_commands_still_pass_gate_two(self):
        commands = ["dir", "type test.txt", "echo hello"]

        with tempfile.TemporaryDirectory() as tmp:
            module = load_s03_module(Path(tmp))

            for command in commands:
                with self.subTest(command=command):
                    self.assertIsNone(
                        module.check_rules("bash", {"command": command})
                    )


if __name__ == "__main__":
    unittest.main()
