"""Self-contained standard-library CSP1/CAV1 codec and persistent CeTTa value-worker client."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import select
import struct
import subprocess
import threading
import time
import uuid

MAX_BYTES = 4 * 1024 * 1024
DEFAULT_WIRE_BYTES = 1024 * 1024
MAX_NODES = 65536
MAX_DEPTH = 64
MAX_VARS = 4096

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Symbol:
    name: str | bytes


@dataclass(frozen=True)
class Var:
    key: int


@dataclass(frozen=True)
class FloatBits:
    bits: int

    @classmethod
    def of(cls, value: float) -> FloatBits:
        return cls(struct.unpack("<Q", struct.pack("<d", value))[0])


@dataclass(frozen=True)
class BigInt:
    decimal: str


@dataclass(frozen=True)
class Rational:
    spelling: str


@dataclass(frozen=True)
class Tensor:
    encoded: bytes

    def __post_init__(self):
        data = bytes(self.encoded)
        if len(data) < 9 or data[:4] != b"CTN1" or data[4] not in (1, 2, 3, 4):
            raise ValueError("invalid tensor header")
        rank = struct.unpack_from("<I", data, 5)[0]
        if rank > (len(data) - 9) // 8:
            raise ValueError("invalid tensor shape")
        shape = struct.unpack_from("<" + "Q" * rank, data, 9)
        size = math.prod(shape) * ({3: 1, 4: 4}.get(data[4], 8))
        if size != len(data) - 9 - 8 * rank:
            raise ValueError("tensor payload size mismatch")
        object.__setattr__(self, "encoded", data)


def S(name: str) -> Symbol:
    return Symbol(name)


def E(head: str, *args) -> tuple:
    return (S(head), *args)


def scalar_float(value) -> float:
    if isinstance(value, FloatBits):
        return struct.unpack("<d", struct.pack("<Q", value.bits))[0]
    return float(value)


def encode(value, *, maximum_bytes: int = MAX_BYTES) -> bytes:
    if type(maximum_bytes) is not int or maximum_bytes < 4:
        raise ValueError("invalid atom byte allowance")
    output = bytearray(b"CAV1")
    variables: dict[int, int] = {}
    nodes = 0

    def append(data: bytes):
        if len(output) + len(data) > maximum_bytes:
            raise ValueError(
                f"atom byte limit: at least {len(output) + len(data)} bytes needed; allowance is {maximum_bytes}"
            )
        output.extend(data)

    def text(tag: bytes, data: str | bytes):
        if isinstance(data, str):
            data = data.encode("utf-8")
        if len(data) > 0xFFFFFFFF:
            raise ValueError("CAV1 field exceeds its 32-bit length encoding")
        if len(output) + 5 + len(data) > maximum_bytes:
            raise ValueError(
                f"atom byte limit: at least {len(output) + 5 + len(data)} bytes needed; allowance is {maximum_bytes}"
            )
        append(tag + struct.pack("<I", len(data)))
        append(data)

    def visit(v, depth: int):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("atom structure limit")
        if isinstance(v, Symbol):
            text(b"S", v.name)
        elif isinstance(v, Var):
            index = variables.setdefault(v.key, len(variables))
            if len(variables) > MAX_VARS:
                raise ValueError("variable limit")
            append(b"V" + struct.pack("<I", index))
        elif isinstance(v, bool):
            append(b"Y" if v else b"N")
        elif isinstance(v, int):
            append(b"I" + struct.pack("<q", v))
        elif isinstance(v, float):
            append(b"F" + struct.pack("<d", v))
        elif isinstance(v, FloatBits):
            append(b"F" + struct.pack("<Q", v.bits))
        elif isinstance(v, (str, bytes)):
            text(b"T", v)
        elif isinstance(v, BigInt):
            text(b"J", v.decimal)
        elif isinstance(v, Rational):
            text(b"Q", v.spelling)
        elif isinstance(v, Tensor):
            text(b"A", v.encoded)
        elif isinstance(v, tuple):
            append(b"E" + struct.pack("<I", len(v)))
            for child in v:
                visit(child, depth + 1)
        else:
            raise TypeError(f"not portable syntax: {type(v).__name__}")

    visit(value, 0)
    return bytes(output)


def decode(data: bytes, *, maximum_bytes: int = MAX_BYTES):
    if type(maximum_bytes) is not int or maximum_bytes < 4:
        raise ValueError("invalid atom byte allowance")
    if len(data) > maximum_bytes or data[:4] != b"CAV1":
        raise ValueError("invalid CAV1 packet")
    pos, nodes, variables = 4, 0, 0

    def take(n: int) -> bytes:
        nonlocal pos
        if n > len(data) - pos:
            raise ValueError("truncated atom")
        result = data[pos : pos + n]
        pos += n
        return result

    def visit(depth: int):
        nonlocal nodes, variables
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("atom structure limit")
        tag = take(1)
        if tag in (b"I", b"F"):
            raw = take(8)
            return struct.unpack("<q", raw)[0] if tag == b"I" else FloatBits(struct.unpack("<Q", raw)[0])
        if tag in (b"Y", b"N"):
            return tag == b"Y"
        if tag == b"V":
            index = struct.unpack("<I", take(4))[0]
            if index > variables or index >= MAX_VARS:
                raise ValueError("invalid variable scope")
            if index == variables:
                variables += 1
            return Var(index)
        if tag == b"E":
            count = struct.unpack("<I", take(4))[0]
            if count > MAX_NODES - nodes or count > len(data) - pos:
                raise ValueError("expression limit")
            return tuple(visit(depth + 1) for _ in range(count))
        if tag == b"A":
            return Tensor(take(struct.unpack("<I", take(4))[0]))
        if tag in (b"S", b"T", b"J", b"Q"):
            raw = take(struct.unpack("<I", take(4))[0])
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                if tag in (b"J", b"Q"):
                    raise ValueError("invalid exact number") from None
                decoded = raw
            if tag == b"S":
                return Symbol(decoded)
            if tag == b"J":
                return BigInt(decoded)
            if tag == b"Q":
                return Rational(decoded)
            return decoded
        raise ValueError("unknown atom tag")

    result = visit(0)
    if pos != len(data):
        raise ValueError("trailing atom bytes")
    return result


class WorkerUnavailable(RuntimeError):
    """Transport failed before request bytes reached the worker, or worker is closed."""


class WorkerReplyUncertain(WorkerUnavailable):
    """Transport failed after request bytes were sent; durable publication may have succeeded."""

    def __init__(self, message: str, *, request_id: str, payload):
        super().__init__(message)
        self.request_id = request_id
        self.payload = payload


class WorkerEvaluationError(RuntimeError):
    """Worker evaluated the request and returned an application-level error reply."""

    def __init__(self, detail):
        self.detail = detail
        super().__init__("worker evaluation error: " + repr(detail))


def gateway_cetta_ref() -> str:
    return (HERE / "GATEWAY_CETTA_REF").read_text().strip()


def resolve_gateway_worker(explicit: str | Path | None = None) -> Path:
    """Locate the gateway-local frozen cetta-value-worker binary without coupling to global CETTA_BIN."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    for env_key in ("METTACLAW_GATEWAY_WORKER_BIN", "CETTA_VALUE_WORKER_BIN"):
        env_val = os.environ.get(env_key)
        if env_val:
            candidates.append(Path(env_val).expanduser())

    short_ref = gateway_cetta_ref()[:8]
    runtime_root = HERE.parent.parent
    candidates.extend([
        runtime_root / ".runtime/cetta/cetta-value-worker",
        runtime_root / "cetta/cetta-value-worker",
        Path.home() / f".local/lib/pettaclaw/cetta-gateway-{short_ref}/cetta-value-worker",
        Path.home() / f".local/lib/pettaclaw/cetta-ours-{short_ref}/cetta-value-worker",
        Path.home() / "repos/CeTTa-ours/runtime/cetta-value-worker",
    ])

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    searched = ", ".join(str(p) for p in candidates)
    raise WorkerUnavailable(f"gateway cetta-value-worker ({short_ref}) not found; checked: {searched}")


class CettaValueWorker:
    """Thread-safe persistent CSP1 binary RPC client to cetta-value-worker."""

    def __init__(
        self,
        binary: str | Path,
        module: str | Path,
        entry: str,
        *,
        timeout: float = 30.0,
        language: str = "petta",
        maximum_bytes: int = MAX_BYTES,
    ):
        if language not in ("petta", "prime"):
            raise ValueError("invalid worker language")
        if type(maximum_bytes) is not int or not 4 <= maximum_bytes <= 0xFFFFFFFF:
            raise ValueError("invalid frame byte allowance")
        self.binary = Path(binary)
        self.module = Path(module)
        self.entry = entry
        self.timeout = timeout
        self.maximum_bytes = maximum_bytes
        self.lock = threading.RLock()
        self.command = [str(self.binary)]
        if maximum_bytes != DEFAULT_WIRE_BYTES:
            self.command += ["--value-bytes", str(maximum_bytes)]
        self.command += ["--lang", language, str(self.module), entry]
        self.closed = False
        self.process: subprocess.Popen | None = None
        self.generation = 0
        self._start()

    def _start(self):
        if self.closed:
            raise WorkerUnavailable("CeTTa worker is closed")
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.generation += 1

    def _discard(self):
        process, self.process = self.process, None
        if process is None:
            return None
        try:
            process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        try:
            process.stdout.close()
        except OSError:
            pass
        return process.returncode

    def ensure_alive(self) -> int:
        with self.lock:
            if self.closed:
                raise WorkerUnavailable("CeTTa worker is closed")
            if self.process is None or self.process.poll() is not None:
                self._discard()
                self._start()
            return self.generation

    def close(self):
        with self.lock:
            self.closed = True
            return self._discard()

    def call(self, value):
        with self.lock:
            if self.closed:
                raise WorkerUnavailable("CeTTa worker is closed")
            if self.process is None or self.process.poll() is not None:
                self._discard()
                self._start()
            request_id = uuid.uuid4().hex
            data = encode(E("work", request_id, value), maximum_bytes=self.maximum_bytes)
            packet = b"CSP1" + struct.pack(">I", len(data)) + data
            deadline = time.monotonic() + self.timeout
            bytes_sent = False

            def transfer(payload: bytes | None = None, size: int = 0) -> bytes:
                nonlocal bytes_sent
                writing = payload is not None
                fd = (self.process.stdin if writing else self.process.stdout).fileno()
                output, offset = bytearray(), 0
                total = len(payload) if writing else size
                while offset < total:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise TimeoutError("CeTTa worker deadline")
                    ready = select.select([] if writing else [fd], [fd] if writing else [], [], left)
                    if not (ready[0] or ready[1]):
                        raise TimeoutError("CeTTa worker deadline")
                    try:
                        chunk = os.write(fd, payload[offset:]) if writing else os.read(fd, total - offset)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        raise EOFError("CeTTa worker stopped")
                    if writing and chunk > 0:
                        bytes_sent = True
                    offset += chunk if writing else len(chunk)
                    if not writing:
                        output.extend(chunk)
                return bytes(output)

            try:
                transfer(packet)
                header = transfer(size=8)
                length = struct.unpack(">I", header[4:])[0]
                if header[:4] != b"CSP1" or not 4 <= length <= self.maximum_bytes:
                    raise ValueError("invalid worker frame")
                raw = transfer(size=length)
                response = decode(raw, maximum_bytes=self.maximum_bytes)
                if (
                    not isinstance(response, tuple)
                    or len(response) != 4
                    or response[:2] != (S("reply"), request_id)
                ):
                    raise ValueError("uncorrelated worker reply")
                if response[2] not in (S("ok"), S("error")):
                    raise ValueError("invalid worker status")
                if response[2] != S("ok"):
                    raise WorkerEvaluationError(response[3])
                return response[3]
            except WorkerEvaluationError:
                raise
            except (OSError, EOFError, ValueError, TimeoutError) as error:
                self._discard()
                msg = f"CeTTa worker transport failed: {type(error).__name__}: {error}"
                if bytes_sent:
                    raise WorkerReplyUncertain(msg, request_id=request_id, payload=value) from error
                raise WorkerUnavailable(msg) from error
            except BaseException:
                self._discard()
                raise
