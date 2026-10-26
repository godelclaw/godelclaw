"""Project05 phase runner; use a fresh evidence directory for every attempt."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import random
import re
import selectors
import subprocess
import sys
import tempfile
import types
import unicodedata
from unittest import mock

from effect_receipts_support import IMPORTS, Qualification, emit, limit, q, rows


def capabilities(c):
    assert unicodedata.unidata_version == "15.0.0"
    directory = c.directory / "writer-root"
    path = directory / "line.jsonl"
    payload = '{"fixture": "日🙂\\u0000"}\n'
    program = IMPORTS + '''
!(println! (Made (fs:make-directories %s 448)))
!(let $writer (fs:open-write %s ("append" "create") 384)
   (let* (($locked (fs:lock $writer exclusive))
          ($written (fs:write-all $writer %s))
          ($synced (fs:sync $writer))
          ($unlocked (fs:unlock $writer))
          ($closed (fs:close $writer))
          ($closed-again (fs:close $writer)))
     (println! (Writer $locked $written $synced $unlocked $closed $closed-again))))
!(println! (Missing (fs:open-write %s ("append" "create") 384)))
!(println! (SurrogateCategory (str:unicode-category 55296)))
!(println! (SurrogateBuild (str:from-codepoints (55296))))
!(println! (SurrogateJson (json:parse "\\\"\\\\ud800\\\"")))
!(let $lines (str:line-new) (println! (SplitLines (str:feed-lines $lines %s True))))
''' % (q(directory), q(path), q(payload), q(c.directory / "absent-parent" / "missing"),
       q("a\r\nb\vc\fd\x1ce\x1df\x1eg\x85h\u2028i\u2029j\n"))
    output = c.run("writer-and-boundaries", program)
    assert path.read_bytes() == payload.encode("utf-8")
    assert "fs:locked" in output and "fs:synced" in output and "fs:unlocked" in output
    assert "fs:closed" in output and "fs:failed" in output
    assert '(SurrogateCategory "Cs")' in output
    assert "LinesCollectedV1" in output and "StrRefusedV1" not in output
    c.save("writer-bytes.json", {"hex": path.read_bytes().hex(),
                                "file_mode": path.stat().st_mode & 0o777,
                                "root_mode": directory.stat().st_mode & 0o777})
    samples = [0, 10, 0x200d, 0x378, 0xd800, 0xdbff, 0xdc00, 0xdfff,
               0xe000, 0x1fae8, 0x10ffff]
    rng = random.Random(505)
    samples = sorted(set(samples + [rng.randrange(0x110000) for _ in range(2048)]))
    expected = [unicodedata.category(chr(n)) for n in samples]
    actual = rows(c.run("unicode-categories", IMPORTS + emit('(str:unicode-version)') +
                        "".join(emit('(str:unicode-category %d)' % n) for n in samples)))
    assert actual[0] == unicodedata.unidata_version and actual[1:] == expected
    c.save("unicode-comparison.json", {"unidata_version": unicodedata.unidata_version,
                                      "codepoints": samples, "expected": expected, "actual": actual[1:]})
    uuids = rows(c.run("uuid4", IMPORTS + "".join(emit('(system:uuid4-hex)') for _ in range(256))))
    assert len(uuids) == len(set(uuids)) == 256
    assert all(re.fullmatch(r"[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}", s) for s in uuids)
    c.save("uuid4-observations.json", uuids)
    crossing = c.directory / "crossings.jsonl"
    program = IMPORTS + '!(import! &self ./tests/effect_receipts_probe.py)\n'
    for label, expr in (("native-atom", "shell"), ("command-expression", '(shell "arg" 3 True)'),
                        ("json-list", '(json:parse "[\\\"shell\\\", \\\"arg\\\", 3, true]")'),
                        ("command-string", q('["shell", "arg"]')),
                        ("nul-result", q("A\0B🙂"))):
        passed = '(quote %s)' % expr if label in ("native-atom", "command-expression") else expr
        program += '!(py-call (effect_receipts_probe.crossing %s %s %s))\n' % (q(crossing), q(label), passed)
    c.run("actual-python-crossings", program)
    (c.evidence / "crossings.jsonl").write_bytes(crossing.read_bytes())
    gaps = boundary_semantics(c)
    descriptor_lock(c)
    c.save("capabilities-summary.json", {"writer_payload_equal": True, "unicode_samples": len(samples),
                                         "uuid4_samples": len(uuids), "port_activation": False,
                                         "status": "CAPABILITY_CHECKPOINT", "blocking_gaps": gaps})


def boundary_semantics(c):
    """Required Python shared-state inputs, not a replacement native JSON parser."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    import public_worklog
    import effect_receipts
    root = c.directory / "surrogate-state"
    root.mkdir()
    value = {"enabled": True, "seq": 4, "epoch": 7, "extra": "\ud800"}
    state_path = root / "state.json"
    state_path.write_text(json.dumps(value), encoding="ascii")
    initial = state_path.read_bytes()
    with mock.patch.dict(os.environ, c.environment(), clear=True):
        actual = public_worklog.transact(root, "status")
        cleaned = public_worklog.clean("A\ud800B")
    assert actual == value and cleaned == "A B"
    assert state_path.read_bytes() == initial
    c.save("oracle-surrogate-state.json", {
        "input_hex": initial.hex(), "returned": actual,
        "after_hex": state_path.read_bytes().hex(), "clean_A_surrogate_B": cleaned})
    nonfinite = c.directory / "nonfinite-state"
    nonfinite.mkdir()
    nonfinite_value = {"enabled": False, "seq": 0, "epoch": 0, "extra": float("nan")}
    nonfinite_bytes = json.dumps(nonfinite_value).encode("ascii")
    (nonfinite / "state.json").write_bytes(nonfinite_bytes)
    with mock.patch.dict(os.environ, c.environment(), clear=True):
        returned = public_worklog.transact(nonfinite, "status")
    assert str(returned["extra"]) == "nan"
    assert (nonfinite / "state.json").read_bytes() == nonfinite_bytes
    c.save("oracle-nonfinite-state.json", {"input_hex": nonfinite_bytes.hex(),
                                           "after_hex": (nonfinite / "state.json").read_bytes().hex()})
    text = q(initial.decode("ascii"))
    program = IMPORTS
    for api in ("json:parse", "json:parse-reference", "json:inspect", "json:inspect-reference", "json-parse"):
        program += '!(println! (SurrogateState %s (%s %s)))\n' % (api, api, text)
    program += '!(println! (SurrogateInteger (str:unicode-category 55296)))\n'
    program += '!(println! (NonfiniteState (json:parse %s)))\n' % q('{"enabled": false, "seq": 0, "epoch": 0, "extra": NaN}')
    # Invalid UTF-8 is not a surrogate bridge: strict codepoint operations
    # must refuse it, whereas replacement decoding is explicit for receipt tails.
    raw = c.directory / "invalid-utf8"
    raw.write_bytes(b'A\xed\xa0\x80B\xf0\x9f')
    program += '!(let ($status $raw) (fs:read-text-result %s 0 "raw") (println! (InvalidBytes (str:utf8-error $raw) (str:codepoints $raw))))\n' % q(raw)
    program += '!(println! (ReplacementTail (fs:tail-text %s 0)))\n' % q(raw)
    output = c.run("python-json-surrogate-boundary", program)
    assert output.count("(SurrogateState ") == 5
    assert output.count("JsonInvalidUnicodeEscapeV1") == 5
    assert '(SurrogateInteger "Cs")' in output
    assert "(NonfiniteState (JsonFailureV1" in output
    c.save("shared-state-boundary.json", {"native_stdout": output,
        "python_status_accepts_and_preserves_bytes": True,
        "python_clean_accepts_surrogate": True,
        "note": "Inspect native results before claiming shared-file or clean parity"})
    # The problem also reaches the public projector via ASCII-only command
    # strings: no surrogate-bearing native text is needed to demonstrate it.
    commands = ('["shell", "\\ud800"]', '["shell", NaN]')
    expected = []
    fixture_calls = []
    env = c.environment()
    env["METTACLAW_STREAM_DIR"] = ""
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.dict(
            sys.modules, {"stream_sink": types.SimpleNamespace(
                receipt=lambda *args: fixture_calls.append(args))}), mock.patch.object(
            effect_receipts.time, "time", return_value=1700000000.125):
        for index, command in enumerate(commands):
            occurrence = "fixture:json-command:%d" % index
            event = effect_receipts.public_event(11, "returned", command, "fixture-result", 1700000000.125, occurrence)
            assert event["tool"] == "shell"
            result = effect_receipts.record_command(11, "returned", command, "fixture-result", occurrence)
            assert result == 1
            expected.append({"command": command, "tool_name": effect_receipts._tool_name(command),
                             "event": event, "record_result": result})
    assert len(fixture_calls) == 2
    receipt = Path(env["METTACLAW_EFFECT_RECEIPT_PATH"])
    c.save("oracle-actual-command-inputs.json", {"cases": expected,
        "receipt_hex": receipt.read_bytes().hex(), "stream_fixture_calls": len(fixture_calls)})
    program = IMPORTS
    for index, command in enumerate(commands):
        program += '!(println! (CommandParse %d (json:parse %s)))\n' % (index, q(command))
        program += '!(println! (CommandToolPath %d (json:parse-at %s (0))))\n' % (index, q(command))
    output = c.run("actual-command-json-boundary", program)
    assert output.count("(CommandParse ") == 2 and output.count("(CommandToolPath ") == 2
    assert output.count("(JsonFailureV1 ") == 4
    c.save("actual-command-json-boundary.json", {"native_stdout": output, "oracle_cases": expected,
        "status": "BLOCKING: Python projects allowlisted ASCII command strings that native JSON rejects"})
    # Replacement decoding remains separate from strict/ surrogate semantics.
    program = IMPORTS
    for size in range(9):
        program += '!(let (fs:tail $total $truncated $text) (fs:tail-text %s %d) (println! (Wire (json:stringify-python-with (JsonArray ($total $truncated $text)) ()))))\n' % (q(raw), size)
    tails = rows(c.run("replacement-tail-boundaries", program))
    data = raw.read_bytes()
    expected_tails = [[len(data), bool(size and len(data) > size),
                       (data[-size:] if size else data).decode("utf-8", "replace")]
                      for size in range(9)]
    assert tails == expected_tails, (tails, expected_tails)
    c.save("replacement-tail-comparison.json", {"input_hex": data.hex(), "actual": tails,
                                               "expected": expected_tails})
    return ["Python-compatible JSON ingestion for escaped lone surrogates and NaN",
            "lossless Python string/codepoint representation for surrogate-aware clean and round trips"]


def descriptor_lock(c):
    """Observe a real held native flock from Python, with no sleep/spin."""
    path = c.directory / "held-lock.jsonl"
    source = c.directory / "held-lock.metta"
    program = IMPORTS + '''
!(let $writer (fs:open-write %s ("append" "create") 384)
   (let* (($locked (fs:lock $writer exclusive))
          ($_ (println! LockHeld))
          ($release (system:read-line))
          ($written (fs:write-all $writer "native\\n"))
          ($synced (fs:sync $writer))
          ($unlocked (fs:unlock $writer))
          ($closed (fs:close $writer)))
     (println! (LockDone $locked $written $synced $unlocked $closed))))
''' % q(path)
    source.write_text(program)
    (c.evidence / "held-lock.metta").write_text(program)
    command = [str(c.binary), "--lang", "petta", str(source)]
    env = c.environment()
    process = subprocess.Popen(command, cwd=c.directory, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    prefix = b""
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "native lock readiness timed out"
            prefix = process.stdout.readline()
        assert prefix == b"LockHeld\n", prefix
        with path.open("ab") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                blocked = True
            else:
                blocked = False
                fcntl.flock(stream, fcntl.LOCK_UN)
        assert blocked, "Python acquired same-inode lock while native claimed to hold it"
        out, err = process.communicate(b"release\n", timeout=10)
    except BaseException:
        process.terminate()
        out, err = process.communicate(timeout=10)
        c.record_process("held-lock-failed", command,
                         subprocess.CompletedProcess(command, process.returncode, prefix + out, err), env)
        raise
    c.record_process("held-lock", command,
                     subprocess.CompletedProcess(command, process.returncode, prefix + out, err), env)
    assert process.returncode == 0 and path.read_bytes() == b"native\n"
    with path.open("ab") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stream.write(b"python\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream, fcntl.LOCK_UN)
    assert path.read_bytes() == b"native\npython\n"
    c.save("held-lock-observation.json", {"same_inode_python_nonblocking_lock_refused": True,
                                          "python_lock_after_explicit_native_close": True,
                                          "bytes_hex": path.read_bytes().hex()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capabilities",))
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    assert sys.version_info[:3] == (3, 12, 13), sys.version
    limit()
    args.evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="effect05-check-") as temp:
        c = Qualification(args.evidence.resolve(), Path(temp))
        with c.phase(args.phase):
            try:
                capabilities(c)
            except BaseException as exc:
                c.save("phase-failure.json", {"type": type(exc).__name__, "detail": str(exc)})
                raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
