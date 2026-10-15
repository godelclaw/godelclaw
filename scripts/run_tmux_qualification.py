#!/usr/bin/env python3
"""Run deterministic terminal qualification scenarios on an isolated server."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tmux_qualification import IsolatedTmux  # noqa: E402


def require(condition: bool, name: str) -> None:
    if not condition:
        raise AssertionError(name)
    print("PASS", name)


def main() -> int:
    socket_name = "godel-qualification-%d-%s" % (
        os.getpid(), uuid.uuid4().hex[:8]
    )
    world = IsolatedTmux(socket_name)
    try:
        world.start()
        left = world.observe("qualification:left")
        right = world.create_shell_window_after("qualification:left", "right")
        room = world.create_shell_window_after("qualification:left", "room")

        require(
            [entry.rsplit(":", 1)[-1] for entry in world.list_windows()]
            == ["left", "room", "right"],
            "exact insertion between neighboring windows",
        )

        world.unchecked_send(
            room.pane_id,
            "python3 -u -c \"print('GUEST_DONE')\"",
        )
        room_after_guest = world.wait_until(
            room.pane_id,
            lambda observation:
                "GUEST_DONE" in observation.content
                and observation.current_command == "bash",
        )
        require(
            room_after_guest.pane_id == room.pane_id,
            "shell-owned window survives guest completion",
        )
        require(room_after_guest.current_command == "bash",
                "control returns to owning shell")

        # Guest exit can precede the shell's final prompt redraw. Observe a
        # stable screen before testing a send against that exact screen.
        right_before = world.wait_until_stable(right.pane_id)
        room_before = world.wait_for_stable_text(room.pane_id, "GUEST_DONE")
        require(
            world.guarded_send(room_before, "printf 'ROOM_ONLY\\n'").status
            == "sent",
            "exact guarded send accepted",
        )
        world.wait_for_text(room.pane_id, "ROOM_ONLY")
        require(
            world.observe(right.pane_id).content == right_before.content,
            "other-window noninterference",
        )

        stale = world.observe(room.pane_id)
        world.unchecked_send(room.pane_id, "printf 'NEW_STIMULUS\\n'")
        world.wait_for_text(room.pane_id, "NEW_STIMULUS")
        withheld = world.guarded_send(stale, "printf 'MUST_NOT_RUN\\n'")
        require(
            withheld.status == "withheld"
            and withheld.detail == "pane-changed-since-observation",
            "new screen stimulus withholds stale action",
        )
        require(
            "MUST_NOT_RUN" not in world.observe(room.pane_id).content,
            "withheld suffix has no terminal effect",
        )

        world.unchecked_send(
            room.pane_id,
            "python3 -u -c \"input('TRUST_PROMPT>'); "
            "print('SERVER_SELECTION>'); input(); print('IDLE_READY')\"",
        )
        trust = world.wait_for_stable_text(room.pane_id, "TRUST_PROMPT>")
        trust_result = world.guarded_send(trust, "yes")
        require(
            trust_result.status == "sent",
            "dialog action follows observed trust prompt (%s)"
            % trust_result.detail,
        )
        server = world.wait_for_stable_text(
            room.pane_id, "SERVER_SELECTION>"
        )
        server_result = world.guarded_send(server, "lean-lsp")
        require(
            server_result.status == "sent",
            "dialog re-observes before server selection (%s)"
            % server_result.detail,
        )
        world.wait_for_text(room.pane_id, "IDLE_READY")
        require(True, "feedback-sensitive dialog reaches idle state")

        require(left.pane_id != room.pane_id != right.pane_id,
                "pane identities remain distinct")

        stale_identity = world.observe(right.pane_id)
        world.run("kill-window", "-t", right.window_id)
        replacement = world.create_shell_window_after(
            "qualification:room", "replacement"
        )
        replaced_result = world.guarded_send(
            stale_identity, "printf 'WRONG_PANE\\n'"
        )
        require(
            replaced_result.status == "withheld"
            and replaced_result.detail == "target-no-longer-exists",
            "destroyed pane capability cannot target replacement",
        )
        require(
            replacement.pane_id != stale_identity.pane_id,
            "replacement receives a distinct pane identity",
        )
        print("TMUX_QUALIFICATION_OK socket=%s" % socket_name)
        return 0
    finally:
        world.stop()


if __name__ == "__main__":
    raise SystemExit(main())
