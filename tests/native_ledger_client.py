"""One client process of the native-ledger tests: input_ack.py on the native path, an operation at a time.

    python native_ledger_client.py <spec file>

The spec is a Python literal (see test_input_ack_native.py): the socket, the
turns directory and (number, operation) pairs. Operation n waits for the file
done-<n-1> in the turns directory and creates done-<n> after it, so the commit
order across processes is the numbering. Prints one JSON line per answer, one for
the pid of this process's ledger worker and one for its scratch directory. Everything it needs from the
environment is set by the spec; the caller starts it with a clean environment.
"""
import ast
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "src")]
import input_ack
import test_input_ledger_remote as remote


def main():
    spec = ast.literal_eval(Path(sys.argv[1]).read_text())
    turns = Path(spec["turns"])
    with remote.oracle_environment() as directory:
        print(json.dumps(["directory", str(directory)]), flush=True)
        runner = NativeRunner.create(directory, spec["socket"])
        for number, operation in spec["operations"]:
            if number > 0:
                wanted = turns / ("done-%d" % (number - 1))
                deadline = time.monotonic() + 120
                while not wanted.exists():
                    if time.monotonic() > deadline:
                        sys.exit("timed out waiting for " + wanted.name)
                    time.sleep(0.01)
            answer = runner.apply(operation)
            print(json.dumps(["answer", number, answer]), flush=True)
            if number == spec["operations"][0][0]:
                print(json.dumps(["worker", input_ack._ledger[2].process.pid]), flush=True)
            (turns / ("done-%d" % number)).write_text("")


class NativeRunner(remote.Oracle):
    """input_ack.py with METTACLAW_INPUT_LEDGER_SOCKET on `socket`. The SQLite file it names only satisfies
    the gates of input_ack (a strategy path, observed strategy on); the native path never opens it."""
    def __init__(self, path, socket):
        super().__init__(path)
        self.socket = str(socket)

    @classmethod
    def create(cls, directory, socket):
        runner = cls(directory / ("gate-%d.db" % next(cls.numbers)), socket)
        runner.select()
        return runner

    def select(self):
        super().select()
        import os
        os.environ[input_ack.LEDGER_SOCKET] = self.socket

    def state(self):
        raise NotImplementedError("read the native store with RemoteCase.dump()")


if __name__ == "__main__":
    main()
