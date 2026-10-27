"""Native policy qualification, with full bags and isolated load/reference I/O.

Set CETTA_CHANNEL_ROOT and CETTA_VALUE_WORKER explicitly. PROVIDER_POLICY_CASES
can override the portable supplied model-case fixture.
PROVIDER_POLICY_EVIDENCE optionally retains typed observations/child closures.
No runtime checkout discovery, provider calls, or real application database.
"""
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get("PROVIDER_POLICY_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
ENGINE = os.environ.get("CETTA_CHANNEL_ROOT")
BINARY = os.environ.get("CETTA_VALUE_WORKER")
CASES = os.environ.get("PROVIDER_POLICY_CASES", str(ROOT / "tests/fixtures/provider_policy_cases.json"))
if ENGINE and BINARY:
    sys.path.insert(0, str(Path(ENGINE) / "tools"))
    from value_worker_client import Worker
    from space_service_client import E, S, BigInt, encode


def record(kind, **fields):
    row = dict(kind=kind, **fields)
    print(json.dumps(row, sort_keys=True), flush=True)
    directory = os.environ.get("PROVIDER_POLICY_EVIDENCE")
    if directory:
        with (Path(directory) / "observations.jsonl").open("a") as output:
            output.write(json.dumps(row, sort_keys=True) + "\n")


@unittest.skipUnless(ENGINE and BINARY and CASES, "explicit frozen runtime and model cases required")
class ProviderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="provider-policy-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.home = self.directory / "home"
        self.home.mkdir(mode=0o700)
        self.sequence = 0
        self.worker = None
        self.addCleanup(self.close_default_worker)

    def environment(self, **values):
        return dict(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / "config"),
                    XDG_STATE_HOME=str(self.home / "state"), XDG_CACHE_HOME=str(self.home / "cache"),
                    TMPDIR=str(self.directory), PATH="/usr/bin:/bin", LANG="C.UTF-8",
                    PYTHONDONTWRITEBYTECODE="1", **values)

    def make_worker(self, **values):
        self.close_default_worker()
        with patch.dict(os.environ, self.environment(**values), clear=True):
            worker = Worker(BINARY, ROOT / "tests/support/provider_policy_probe.metta",
                            "provider-policy-probe:run", language="petta", import_mode="relative",
                            num_threads=1, timeout=10)
        record("worker-start", pid=worker.process.pid, command=worker.command,
               environment=self.environment(**values))
        return worker

    def close_default_worker(self):
        if self.worker is not None:
            worker, self.worker = self.worker, None
            self.close_worker(worker)

    def close_worker(self, worker):
        process = worker.process
        status = worker.close()
        record("worker-close", pid=process.pid if process else None, exit=status,
               stdin_closed=process.stdin.closed if process else None,
               stdout_closed=process.stdout.closed if process else None)
        self.assertIsNotNone(process, "worker outcome was lost")
        self.assertEqual(status, 0)
        self.assertTrue(process.stdin.closed and process.stdout.closed)

    def call(self, command, worker=None):
        if worker is None:
            if self.worker is None:
                self.worker = self.make_worker()
            worker = self.worker
        bag = worker.call(command)
        record("native", test=self.id(), input=repr(command), input_cav=encode(command).hex(),
               bag=repr(bag), bag_cav=encode(bag).hex(), cardinality=len(bag))
        return bag

    def single(self, bag):
        self.assertIsInstance(bag, tuple)
        self.assertEqual(len(bag), 1, bag)
        return bag[0]

    def label(self, bag):
        value = self.single(bag)
        self.assertIsInstance(value, tuple, value)
        self.assertEqual(len(value), 3, value)
        if value[0] == S("ProviderPolicyRefusedV2"):
            self.assertIn(value[1], tuple(map(S, ("invalid-object", "invalid-mode", "invalid-profile", "invalid-cooldown",
                                                "negative-cooldown", "file-observation", "file-read",
                                                "invalid-json", "invalid-config"))))
            self.assertTrue(value[2], value)
            return "refused"
        self.assertEqual(value[0], S("ProviderPolicyV2"), value)
        self.assertIn(value[1], (S("ProviderManagedV2"), S("ProviderUpstreamV2")), value)
        number = value[2]
        self.assertTrue(type(number) is int or isinstance(number, BigInt), value)
        ns = int(number.decimal) if isinstance(number, BigInt) else number
        self.assertGreaterEqual(ns, 0)
        return ("managed" if value[1] == S("ProviderManagedV2") else "upstream") + ":" + str(ns)

    @staticmethod
    def env_value(case, key):
        return E("ProviderPolicyValueV2", case[key]) if key in case else S("ProviderPolicyAbsentV2")

    def resolve(self, case, expected=None, reason=None, detail=None):
        # Native JSON decoding, not Python-to-MeTTa scalar coercion.
        config = self.single(self.call(E("Parse", json.dumps(case.get("config", {})))))
        command = E("Resolve", case.get("mode", "godelclaw"), config,
                    self.env_value(case, "env_profile"), self.env_value(case, "env_delay"))
        bag = self.call(command)
        label = self.label(bag)
        if expected is not None:
            self.assertEqual(label, expected, case)
        if reason:
            self.assertEqual(bag[0][1], S(reason), bag)
        if detail:
            self.assertEqual(bag[0][2], detail, bag)
        return label

    def load(self, expected, *, mode="godelclaw", reason=None, reference=False,
             reference_expected=None, **environment):
        worker = self.make_worker(**environment)
        try:
            bag = self.call(E("Load", mode), worker)
            self.assertEqual(self.label(bag), expected)
            if reason:
                self.assertEqual(bag[0][1], S(reason), bag)
        finally:
            self.close_worker(worker)
        if reference or reference_expected is not None:
            result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "tests/provider_policy_reference.py"),
                                     str(ROOT / "src/llm_policy.py"), mode],
                                    env=self.environment(**environment), cwd=self.directory,
                                    capture_output=True, text=True, timeout=10)
            record("reference", command=result.args, exit=result.returncode,
                   stdout=result.stdout, stderr=result.stderr, environment=self.environment(**environment))
            self.assertEqual(result.returncode, 0)
            answer = json.loads(result.stdout)
            label = ("refused" if "refused" in answer else answer["profile"] + ":" +
                     str(answer["quota_cooldown_seconds"] * 1000000000))
            self.assertEqual(label, expected if reference_expected is None else reference_expected)
            if reference_expected is not None:
                record("intentional-difference", native=expected, reference=label, environment=environment)
        return bag

    def policy_file(self, value=None, *, text=None):
        self.sequence += 1
        path = self.directory / ("policy-" + str(self.sequence) + ".json")
        path.write_text(json.dumps(value) if text is None else text)
        self.retain_fixture(path)
        return str(path)

    def retain_fixture(self, path):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        record("fixture", test=self.id(), path=str(path), bytes_hex=data.hex(), sha256=digest)
        directory = os.environ.get("PROVIDER_POLICY_EVIDENCE")
        if directory:
            archive = Path(directory) / "fixtures"
            archive.mkdir(exist_ok=True)
            (archive / digest).write_bytes(data)

    def test_01_all_thirteen_supplied_model_cases(self):
        cases = json.loads(Path(CASES).read_text())
        self.assertEqual(len(cases), 13)
        for case in cases:
            with self.subTest(case=case["case"]):
                result = self.resolve(case, case["expected"])
                record("model-correspondence", case=case["case"], expected=case["expected"], actual=result)

    def test_02_full_precedence_matrix_and_default_modes(self):
        for flags in itertools.product((False, True), repeat=6):
            global_p, env_p, mode_p, global_d, mode_d, env_d = flags
            config, override, case = {}, {}, {}
            if global_p:
                config["profile"] = "upstream"
            if env_p:
                case["env_profile"] = "managed"
            if mode_p:
                override["profile"] = "upstream"
            if global_d:
                config["quota_cooldown_seconds"] = 11
            if mode_d:
                override["quota_cooldown_seconds"] = 7
            if env_d:
                case["env_delay"] = "3"
            config["modes"] = {"godelclaw": override}
            case["config"] = config
            profile = "upstream" if mode_p or (global_p and not env_p) else "managed"
            seconds = 3 if env_d else 7 if mode_d else 11 if global_d else 300
            self.resolve(case, profile + ":" + str(seconds * 1000000000))
        for mode in ("iter", "omega", "godelclaw", "coding", "", "ITER"):
            profile = "upstream" if mode in ("iter", "omega") else "managed"
            self.resolve(dict(mode=mode), profile + ":300000000000")

    def test_03_object_shapes_even_when_shadowed_and_unselected_ignored(self):
        for bad in (None, [], True, 7, "bad"):
            for config, detail in ((bad, "config"), ({"modes": bad}, "modes"),
                                   ({"modes": {"godelclaw": bad}}, E("ProviderPolicyFieldV2", "modes", "godelclaw"))):
                self.resolve(dict(config=config, env_profile="managed", env_delay="3"),
                             "refused", "invalid-object", detail)
            self.resolve(dict(config={"modes": {"other": bad}, "unknown": bad}), "managed:300000000000")

    def test_04_invalid_selected_scalars_and_shadowed_lower_priorities(self):
        for bad in (None, "", "MANAGED", "invalid", 0, False, [], {}):
            self.resolve(dict(config={"profile": bad}), "refused", "invalid-profile", "profile")
            self.resolve(dict(config={"profile": bad}, env_profile="upstream"), "upstream:300000000000")
            self.resolve(dict(config={"modes": {"godelclaw": {"profile": bad}}}, env_profile="managed"),
                         "refused", "invalid-profile", E("ProviderPolicyFieldV2", "modes", "godelclaw", "profile"))
        for bad in (None, True, False, 1.0, 1.5, "3", [], {}):
            self.resolve(dict(config={"quota_cooldown_seconds": bad}), "refused", "invalid-cooldown")
            self.resolve(dict(config={"quota_cooldown_seconds": bad}, env_delay="2"), "managed:2000000000")
            self.resolve(dict(config={"modes": {"godelclaw": {"quota_cooldown_seconds": bad}}}),
                         "refused", "invalid-cooldown")
        for text in ("", " ", "+", "-", "1.0", "1e3", "1_000", "1 2", "0x10", "NaN", "--1", "+ 1", "3\x00"):
            self.resolve(dict(env_delay=text), "refused", "invalid-cooldown", "METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS")
        for text, seconds in (("0", 0), ("-0", 0), (" +0007\t", 7), ("\r\n42\v\f", 42),
                              (str(10 ** 100), 10 ** 100)):
            self.resolve(dict(env_delay=text), "managed:" + str(seconds * 1000000000))
        for seconds in (0, 1, 2 ** 63, 10 ** 100):
            self.resolve(dict(config={"quota_cooldown_seconds": seconds}), "managed:" + str(seconds * 1000000000))
        for case in (dict(env_delay=" -1 "), dict(config={"quota_cooldown_seconds": -1})):
            self.resolve(case, "refused", "negative-cooldown")
        self.resolve(dict(config={"profile": None, "quota_cooldown_seconds": None,
                                  "modes": {"godelclaw": {"profile": "upstream", "quota_cooldown_seconds": 4}}},
                          env_profile="", env_delay="2"), "upstream:2000000000")

    def test_05_actual_load_environment_files_and_reference(self):
        for mode in ("godelclaw", "iter", "omega", "coding"):
            expected = ("upstream" if mode in ("iter", "omega") else "managed") + ":300000000000"
            self.load(expected, mode=mode, reference=True)
            self.load(expected, mode=mode, reference=True, METTACLAW_LLM_POLICY_PATH="")
        for name, value, expected in (("METTACLAW_LLM_POLICY", "", "refused"),
                                      ("METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS", "", "refused"),
                                      ("METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS", "0", "managed:0"),
                                      ("METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS", "-1", "refused"),
                                      ("METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS", " +007 ", "managed:7000000000"),
                                      ("METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS", str(10 ** 100), "managed:" + str(10 ** 109)),
                                      ("METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS", "1.0", "refused")):
            self.load(expected, reference=True, **{name: value})
        # Replay every supplied case through real load as well as the pure API.
        for case in json.loads(Path(CASES).read_text()):
            env = {"METTACLAW_LLM_POLICY_PATH": self.policy_file(case.get("config", {}))}
            for field, name in (("env_profile", "METTACLAW_LLM_POLICY"),
                                ("env_delay", "METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS")):
                if field in case:
                    env[name] = case[field]
            self.load(case["expected"], mode=case.get("mode", "godelclaw"), reference=True, **env)
        for text in ("{", "", '{"profile":', '{"x":NaN}', '{"x":1} trailing'):
            self.load("refused", reason="invalid-json", METTACLAW_LLM_POLICY_PATH=self.policy_file(text=text))
        for value in (None, [], True, 3, "managed", {"modes": None}, {"modes": {"godelclaw": []}}):
            self.load("refused", reference=True, METTACLAW_LLM_POLICY_PATH=self.policy_file(value))
        # Native contract deliberately refuses coercions Python's int accepts.
        for value, seconds in ((True, 1), (False, 0), (1.0, 1), (1.5, 1), ("3", 3)):
            self.load("refused", reason="invalid-cooldown", reference_expected="managed:" + str(seconds * 1000000000),
                      METTACLAW_LLM_POLICY_PATH=self.policy_file({"quota_cooldown_seconds": value}))
        self.load("refused", reason="invalid-cooldown", reference_expected="managed:1000000000000",
                  METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS="1_000")
        for value in (None, "", "bad", -1):
            self.load("refused", reference=True,
                      METTACLAW_LLM_POLICY_PATH=self.policy_file({"quota_cooldown_seconds": value}))
        self.load("managed:300000000000", METTACLAW_ACTIVE_LOOP_MODE="omega",
                  METTACLAW_LLM_LEDGER_PATH=str(self.directory / "must-not-exist.sqlite3"))
        self.assertFalse((self.directory / "must-not-exist.sqlite3").exists())

    def test_06_file_boundaries_and_no_blocking_nonfile_reads(self):
        denied = Path(self.policy_file({}))
        denied.chmod(0)
        self.addCleanup(denied.chmod, 0o600)
        fifo = self.directory / "fifo"
        os.mkfifo(fifo, 0o600)
        for path in (self.directory / "nonexistent", self.directory, fifo, denied / "child"):
            self.load("refused", reason="file-observation", METTACLAW_LLM_POLICY_PATH=str(path))
        self.load("refused", reason="file-read", reference=True, METTACLAW_LLM_POLICY_PATH=str(denied))
        malformed_utf8 = self.directory / "bad-utf8"
        malformed_utf8.write_bytes(b'{"x":"\xff"}')
        self.retain_fixture(malformed_utf8)
        self.load("refused", reason="invalid-json", METTACLAW_LLM_POLICY_PATH=str(malformed_utf8))

    def test_07_pure_resolver_inert_text_and_unrelated_environment(self):
        sentinel = self.directory / "not-executed"
        text = '(fs:write "' + str(sentinel) + '" "bad")'
        self.resolve(dict(config={"unknown": text, "modes": {"unselected": text}}), "managed:300000000000")
        self.resolve(dict(config={"profile": text}), "refused", "invalid-profile")
        self.resolve(dict(env_delay=text), "refused", "invalid-cooldown")
        worker = self.make_worker(METTACLAW_LLM_POLICY_PATH=str(self.directory / "does-not-exist"),
                                  METTACLAW_LLM_POLICY="invalid", METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS="-1")
        try:
            bag = self.call(E("Resolve", "godelclaw", E("JsonObject", ()),
                              S("ProviderPolicyAbsentV2"), S("ProviderPolicyAbsentV2")), worker)
            self.assertEqual(self.label(bag), "managed:300000000000")
        finally:
            self.close_worker(worker)
        self.load("refused", METTACLAW_LLM_POLICY=text)
        self.load("managed:300000000000", METTACLAW_LLM_POLICY_PATH=self.policy_file({"unknown": text}))
        self.assertFalse(sentinel.exists())

    def test_08_negative_controls_catch_duplicates_unreduced_and_wrong_labels(self):
        duplicate = self.call(S("DuplicateControl"))
        self.assertEqual(len(duplicate), 2)
        with self.assertRaises(AssertionError):
            self.label(duplicate)
        unreduced = self.call(S("UnreducedControl"))
        with self.assertRaises(AssertionError):
            self.label(unreduced)
        with self.assertRaises(AssertionError):
            self.resolve({}, "upstream:300000000000")
        record("negative-controls", duplicates="rejected", unreduced="rejected", wrong_label="rejected")

    def test_09_native_absence_is_not_a_reserved_string_and_json_duplicates(self):
        self.assertEqual(self.single(self.call(E("Environment", "POLICY_TEST_UNSET"))), S("ProviderPolicyAbsentV2"))
        for text in ("", "ProviderPolicyAbsentV2"):
            worker = self.make_worker(POLICY_TEST_UNSET=text)
            try:
                self.assertEqual(self.single(self.call(E("Environment", "POLICY_TEST_UNSET"), worker)), text)
            finally:
                self.close_worker(worker)
        self.load("refused", METTACLAW_LLM_POLICY="ProviderPolicyAbsentV2")
        self.load("upstream:300000000000", reference=True,
                  METTACLAW_LLM_POLICY_PATH=self.policy_file(text='{"profile":"managed","profile":"upstream"}'))

    def test_10_load_full_precedence_matrix_with_offline_reference(self):
        for flags in itertools.product((False, True), repeat=6):
            global_p, env_p, mode_p, global_d, mode_d, env_d = flags
            config, override, env = {}, {}, {}
            if global_p:
                config["profile"] = "upstream"
            if env_p:
                env["METTACLAW_LLM_POLICY"] = "managed"
            if mode_p:
                override["profile"] = "upstream"
            if global_d:
                config["quota_cooldown_seconds"] = 11
            if mode_d:
                override["quota_cooldown_seconds"] = 7
            if env_d:
                env["METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS"] = "3"
            config["modes"] = {"godelclaw": override}
            env["METTACLAW_LLM_POLICY_PATH"] = self.policy_file(config)
            profile = "upstream" if mode_p or (global_p and not env_p) else "managed"
            seconds = 3 if env_d else 7 if mode_d else 11 if global_d else 300
            self.load(profile + ":" + str(seconds * 1000000000), reference=True, **env)

    def test_11_nonstring_modes_are_explicitly_refused(self):
        for mode in (S("iter"), S("omega"), 1, True, E("Mode", "iter")):
            with self.subTest(mode=mode):
                for command in (E("Resolve", mode, E("JsonObject", ()), S("ProviderPolicyAbsentV2"),
                                  S("ProviderPolicyAbsentV2")), E("Load", mode)):
                    bag = self.call(command)
                    self.assertEqual(self.single(bag), E("ProviderPolicyRefusedV2", S("invalid-mode"), "mode"))


if __name__ == "__main__":
    if not (ENGINE and BINARY and CASES):
        raise SystemExit("CETTA_CHANNEL_ROOT and CETTA_VALUE_WORKER are required")
    for path in (BINARY, str(Path(ENGINE) / "tools/value_worker_client.py"),
                 str(ROOT / "src/provider_policy_cetta.metta"), str(ROOT / "tests/support/provider_policy_probe.metta"),
                 str(ROOT / "tests/provider_policy_reference.py"),
                 str(ROOT / "src/llm_policy.py"), CASES, __file__):
        record("identity", path=str(Path(path).resolve()), sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
    unittest.main(verbosity=2)
