from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
S16_PATH = REPO_ROOT / "s16_team_protocols" / "code.py"


def load_s16(temp_cwd: Path):
    fake_anthropic = types.ModuleType("anthropic")

    class FakeAnthropic:
        def __init__(self, *args, **kwargs):
            self.messages = types.SimpleNamespace(create=None)

    fake_dotenv = types.ModuleType("dotenv")
    fake_anthropic.Anthropic = FakeAnthropic
    fake_dotenv.load_dotenv = lambda override=True: None

    previous_anthropic = sys.modules.get("anthropic")
    previous_dotenv = sys.modules.get("dotenv")
    previous_cwd = Path.cwd()
    previous_model = os.environ.get("MODEL_ID")

    spec = importlib.util.spec_from_file_location("s16_under_test", S16_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {S16_PATH}")
    module = importlib.util.module_from_spec(spec)

    sys.modules["anthropic"] = fake_anthropic
    sys.modules["dotenv"] = fake_dotenv
    sys.modules[spec.name] = module
    os.environ["MODEL_ID"] = "test-model"
    try:
        os.chdir(temp_cwd)
        spec.loader.exec_module(module)
        return module
    finally:
        os.chdir(previous_cwd)
        if previous_anthropic is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = previous_anthropic
        if previous_dotenv is None:
            sys.modules.pop("dotenv", None)
        else:
            sys.modules["dotenv"] = previous_dotenv
        if previous_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = previous_model


def text_response(text: str):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
    )


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_plan_approval_wakes_idle_teammate_without_empty_user_message():
    with tempfile.TemporaryDirectory() as tmp:
        module = load_s16(Path(tmp))
        module.time = types.SimpleNamespace(
            sleep=lambda _seconds: time.sleep(0.01),
            time=time.time,
        )

        first_call = threading.Event()
        second_call = threading.Event()
        captured_messages = []

        def create(**kwargs):
            captured_messages.append(list(kwargs["messages"]))
            if len(captured_messages) == 1:
                first_call.set()
                return text_response("Waiting for plan review.")
            if len(captured_messages) == 2:
                second_call.set()
                return text_response("Approval received.")
            raise AssertionError("unexpected extra LLM call")

        module.client.messages.create = create
        module.spawn_teammate_thread("bob", "developer", "Submit a plan.")

        assert first_call.wait(1)
        module.BUS.send(
            "lead",
            "bob",
            "Approved",
            "plan_approval_response",
            {"request_id": "req_test", "approve": True},
        )

        assert second_call.wait(2), "plan approval should resume the idle teammate"
        resumed_messages = captured_messages[1]
        assert any(
            message.get("role") == "user"
            and message.get("content") == "[Plan approved] Proceed with the task."
            for message in resumed_messages
        )
        assert not any(
            message.get("role") == "user" and message.get("content") == []
            for message in resumed_messages
        )

        module.BUS.send(
            "lead",
            "bob",
            "Shut down",
            "shutdown_request",
            {"request_id": "req_shutdown"},
        )
        assert wait_until(lambda: "bob" not in module.active_teammates)

        lead_messages = module.BUS.read_inbox("lead")
        assert any(
            message.get("type") == "shutdown_response"
            and message.get("metadata", {}).get("request_id") == "req_shutdown"
            for message in lead_messages
        )
