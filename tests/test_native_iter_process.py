"""The iter process policy runs natively: no Python executor on its path."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeIterProcessSourceTests(unittest.TestCase):
    def test_runtime_path_has_no_python_iter_executor(self):
        policy = (ROOT / "src" / "iter_process_policy.metta").read_text(
            encoding="utf-8"
        )
        library = (ROOT / "lib_mettaclaw.metta").read_text(encoding="utf-8")
        self.assertNotIn("py-call", policy)
        self.assertNotIn("iter_process_adapter.py", library)



if __name__ == "__main__":
    unittest.main()
