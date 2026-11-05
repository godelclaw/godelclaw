#!/usr/bin/env python3
"""Run unittest modules with METTACLAW_INPUT_LEDGER_SOCKET on a temporary native ledger server.

    CETTA_BIN=<cetta> python3 run_with_ledger_server.py test_input_ack test_input_ledger ...

Starts src/input_ledger_server.metta (CETTA_DB_SERVER_MODULE overrides it) on a
fresh store in a new temporary directory, runs `python3 -m unittest <modules>`
in a child process whose environment has METTACLAW_INPUT_LEDGER_SOCKET set to
that server's socket, stops the server, checks that no process of the run is
left and removes the directory it made. Exit status: that of unittest, else 1.
Never touches any other ledger or state directory.

The socket reaches the test processes in the one way tests/hermetic_env.py honours: the
temporary directory (0700, /tmp/il-run-*) gets the marker file ".godel-test-ledger" holding
the socket path, and the child environment gets GODEL_TEST_LEDGER_SOCKET (checked by the
helper against that marker) and METTACLAW_INPUT_LEDGER_SOCKET. A socket from anywhere else,
such as an exported live one, is removed by the helper in every test module.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hermetic_env  # noqa: E402,F401  (this process too: no inherited state variable reaches the server or the child)
from test_input_ledger_remote import DbServer, available, ENGINE


def main(modules):
    if not available():
        print("set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)", file=sys.stderr)
        return 1
    directory = Path(tempfile.mkdtemp(prefix="il-run-", dir="/tmp"))
    server = DbServer(directory)
    status = 1
    try:
        server.start()
        (directory / hermetic_env.HARNESS_MARKER).write_text(str(server.socket))
        environment = dict(os.environ, METTACLAW_INPUT_LEDGER_SOCKET=str(server.socket),
                           **{hermetic_env.HARNESS_SOCKET: str(server.socket)})
        print("METTACLAW_INPUT_LEDGER_SOCKET=%s (server pid %d, store %s)" % (server.socket, server.process.pid, server.store),
              flush=True)
        status = subprocess.run([sys.executable, "-m", "unittest", *modules], env=environment, cwd=HERE).returncode
    finally:
        code = server.stop()
        gone = all(not Path("/proc/%d" % pid).exists() for pid in server.pids)
        shutil.rmtree(directory, True)
        print("server stopped (exit %s); process gone: %s" % (code, gone), flush=True)
        if not gone:
            status = status or 1
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["test_input_ack", "test_input_ledger", "test_input_ledger_remote"]))
