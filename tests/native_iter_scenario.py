"""Deterministic state for the native Iter multi-turn runtime probe."""

from __future__ import annotations

import os
from pathlib import Path

import helper
import synthetic_llm


APPEND_TRANSFORM = """\
(= (iter-transform
      (coding-view $messages $advertised))
   (success
     (coding-view
       (coding-message-append-one
         $messages (coding-message user %s))
       $advertised)))
"""

FAIL_TRANSFORM = "(= (iter-transform $visible) failure)\n"

TIMEOUT_TRANSFORM = """\
(= (native-iter-scenario-spin) (native-iter-scenario-spin))
(= (iter-transform $visible) (native-iter-scenario-spin))
"""


_prompts: list[str] = []


def _directory() -> Path:
    return Path(os.environ["METTACLAW_ITER_PROCESS_DIR"])


def _replace_transformations(files: dict[str, str]) -> int:
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.glob("*.metta"):
        path.unlink()
    for name, source in files.items():
        (directory / name).write_text(source, encoding="utf-8")
    return 1


def select_stage(stage: str) -> int:
    if stage == "first":
        return _replace_transformations({
            "10_first.metta": APPEND_TRANSFORM % "iter-stage-first",
        })
    if stage == "failure":
        return _replace_transformations({
            "10_failure.metta": FAIL_TRANSFORM,
            "20_after_failure.metta": APPEND_TRANSFORM % "iter-stage-second",
        })
    if stage == "timeout":
        return _replace_transformations({
            "10_timeout.metta": TIMEOUT_TRANSFORM,
        })
    if stage == "fourth":
        return _replace_transformations({
            "10_fourth.metta": APPEND_TRANSFORM % "iter-stage-fourth",
        })
    raise ValueError(f"unknown native Iter scenario stage: {stage}")


def _chat(*args) -> str:
    import cognitive_health
    cognitive_health.turn_started("probe")
    _prompts.append(str(args[-1]))
    responses = (
        '((help "send"))',
        '((help "pin"))',
        '((help "mode"))',
        "((nop))",
    )
    response = responses[min(len(_prompts) - 1, len(responses) - 1)]
    cognitive_health.turn_completed(len(response.encode("utf-8")))
    return response


def install() -> int:
    _prompts.clear()
    synthetic_llm.chat = _chat
    helper._working_boot_cache = {}
    helper.working_boot = lambda: 1
    return 1


def verdict() -> int:
    if len(_prompts) != 4:
        return 0
    first, second, third, fourth = _prompts
    all_prompts = "\n".join(_prompts)

    def has_help(prompt: str, command: str) -> bool:
        # CeTTa renders the command's usage text.  PeTTa preserves the
        # structured help result.  Both prove that the previous turn's
        # command result crossed the process boundary.
        return (f"usage: ({command}" in prompt or
                ("api:help" in prompt and command in prompt))

    def observed_failure(prompt: str, filename: str) -> bool:
        # CeTTa preserves JSON-style quotes around the filename; PeTTa's
        # projection prints the same atom without them.
        return (f"{filename} failure" in prompt or
                f'"{filename}" failure' in prompt)

    checks = {
        "persona": all(
            "GODEL_NATIVE_ITER_PERSONA" in prompt for prompt in _prompts),
        "pins": all("GODEL_NATIVE_ITER_PIN" in prompt for prompt in _prompts),
        "first-visible": "user: iter-stage-first" in first,
        "first-is-snapshot": "iter-stage-second" not in first,
        "second-visible": "user: iter-stage-second" in second,
        "second-replaces-first": "iter-stage-first" not in second,
        "failure-observed": observed_failure(second, "10_failure.metta"),
        "first-feedback": has_help(second, "send"),
        "timeout-stutters-first": "iter-stage-first" not in third,
        "timeout-stutters-second": "iter-stage-second" not in third,
        "timeout-precedes-fourth": "iter-stage-fourth" not in third,
        "timeout-observed": observed_failure(third, "10_timeout.metta"),
        "timeout-detail": "timeout" in third,
        "second-feedback": has_help(third, "pin"),
        "fourth-visible": "user: iter-stage-fourth" in fourth,
        "fourth-replaces-first": "iter-stage-first" not in fourth,
        "fourth-replaces-second": "iter-stage-second" not in fourth,
        "observations-cross-boundary": "ITER_PROCESS_OBSERVATIONS" in all_prompts,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        print("NATIVE_ITER_SCENARIO_FAILED " + " ".join(failed))
        print("NATIVE_ITER_PERSONA_PRESENCE " + repr([
            "GODEL_NATIVE_ITER_PERSONA" in prompt for prompt in _prompts
        ]))
        print("NATIVE_ITER_FIRST_PROMPT " + repr(first[:500]))
    return int(not failed)
