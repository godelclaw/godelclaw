"""Each fuel discipline is a different claim about what a budget is for."""

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fuel_modes  # noqa: E402


FULL = 50


class FuelModesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = pathlib.Path(self.temporary.name) / "fuel_mode.json"
        self.environment = mock.patch.dict(
            os.environ, {"METTACLAW_FUEL_MODE_PATH": str(self.path)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def select(self, name):
        self.assertIn("fuel set to", fuel_modes.set_fuel(name))

    def test_default_is_decay(self):
        self.assertEqual(fuel_modes.current_fuel(), "decay")
        self.assertEqual(fuel_modes.ceiling(FULL), fuel_modes.CARRY * FULL)

    def test_saturate_is_the_loops_original_behaviour(self):
        self.select("saturate")
        self.assertEqual(fuel_modes.refuel(0, FULL), FULL)
        self.assertEqual(fuel_modes.refuel(30, FULL), FULL)
        # An unspent burst is not carried: the ceiling is one full breath.
        self.assertEqual(fuel_modes.refuel(FULL, FULL), FULL)
        self.assertEqual(fuel_modes.ceiling(FULL), FULL)

    def test_accumulate_compounds_without_limit(self):
        self.select("accumulate")
        held = 0
        for _ in range(10):
            held = fuel_modes.refuel(held, FULL)
        self.assertEqual(held, 10 * FULL)
        self.assertEqual(fuel_modes.ceiling(FULL), 0)

    def test_unlimited_is_ended_by_policy_not_turn_count(self):
        self.select("unlimited")
        held = fuel_modes.refuel(0, FULL)
        for _ in range(10_000):
            held = fuel_modes.spend(held)
        self.assertEqual(held, FULL)
        self.assertEqual(fuel_modes.ceiling(FULL), 0)

    def test_carry_accumulates_up_to_its_ceiling(self):
        self.select("carry")
        held = 0
        for _ in range(20):
            held = fuel_modes.refuel(held, FULL)
        self.assertEqual(held, fuel_modes.CARRY * FULL)
        self.assertEqual(fuel_modes.ceiling(FULL), fuel_modes.CARRY * FULL)

    def test_decay_shares_the_carry_ceiling(self):
        self.select("decay")
        ceiling = fuel_modes.CARRY * FULL
        held = 0
        for _ in range(60):
            held = fuel_modes.refuel(held, FULL)
            self.assertLessEqual(held, ceiling)
        # Integer truncation settles just below the real fixed point 4*grant.
        self.assertEqual(held, 197)
        self.assertEqual(fuel_modes.ceiling(FULL), ceiling)
        # The bound is tight: it is reached from the ceiling itself.
        self.assertEqual(fuel_modes.refuel(ceiling, FULL), ceiling)

    def test_carry_and_decay_agree_on_the_limit_not_the_path(self):
        self.select("carry")
        carry = [0]
        for _ in range(6):
            carry.append(fuel_modes.refuel(carry[-1], FULL))
        self.select("decay")
        decay = [0]
        for _ in range(6):
            decay.append(fuel_modes.refuel(decay[-1], FULL))
        self.assertEqual(fuel_modes.ceiling(FULL), fuel_modes.CARRY * FULL)
        self.assertNotEqual(carry, decay, "the two paths must differ")
        # carry runs to the ceiling at full rate; decay approaches it.
        self.assertEqual(carry[4], fuel_modes.CARRY * FULL)
        self.assertLess(decay[4], fuel_modes.CARRY * FULL)

    def test_no_discipline_starves_a_renewal(self):
        for name in fuel_modes.FUELS:
            self.select(name)
            for held in (0, 1, 7, FULL, 500):
                self.assertGreaterEqual(
                    fuel_modes.refuel(held, FULL), FULL,
                    "%s must never grant less than the offered burst" % name)

    def test_bounded_disciplines_spend_one_turn(self):
        for name in ("saturate", "accumulate", "carry", "decay"):
            self.select(name)
            self.assertEqual(fuel_modes.spend(7), 6)
            self.assertEqual(fuel_modes.spend(0), 0)

    def test_selection_persists_and_rejects_nonsense(self):
        self.select("carry")
        self.assertEqual(fuel_modes.current_fuel(), "carry")
        self.assertIn("fuel-set failed", fuel_modes.set_fuel("turbo"))
        self.assertEqual(fuel_modes.current_fuel(), "carry")

    def test_unreadable_state_falls_back_to_the_default(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(fuel_modes.current_fuel(), fuel_modes.DEFAULT)

    def test_boundary_values_never_raise(self):
        for name in fuel_modes.FUELS:
            self.select(name)
            self.assertEqual(fuel_modes.refuel("x", "y"), 0)
            self.assertEqual(fuel_modes.refuel(-5, FULL), FULL)

    def test_views_mark_the_active_discipline(self):
        self.select("decay")
        self.assertIn("active fuel: decay", fuel_modes.fuel_view())
        self.assertIn("● decay", fuel_modes.fuels_view())
        self.assertIn("  saturate", fuel_modes.fuels_view())


if __name__ == "__main__":
    unittest.main(verbosity=2)
