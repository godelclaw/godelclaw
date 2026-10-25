"""Fast checks of the benchmark's independent Git trace witness."""
import unittest

from small_views_cold_probe import git_events


class ColdWitnessTest(unittest.TestCase):
    def test_pairs_real_invocations_and_exit_codes(self):
        self.assertEqual(git_events(["start\t42\t-C\tfixture\trev-parse\tHEAD", "end\t42\t0"]),
                         [{"pid": "42", "argv": ["-C", "fixture", "rev-parse", "HEAD"],
                           "returncode": 0}])

    def test_cache_hit_is_observed_as_no_probes(self):
        self.assertEqual(git_events([]), [])

    def test_incomplete_or_overlapping_traces_cannot_pass(self):
        for trace in (["start\t42\tstatus"], ["end\t42\t0"],
                      ["start\t42\tstatus", "end\t43\t0"],
                      ["start\t42\tstatus", "start\t43\tstatus"], ["unknown"]):
            with self.subTest(trace=trace), self.assertRaises(AssertionError):
                git_events(trace)


if __name__ == "__main__":
    unittest.main(verbosity=2)
