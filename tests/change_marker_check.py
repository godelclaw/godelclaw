"""Checks for change_marker_turn_probe.metta."""


def one_turn(first, second):
    """1 when the second of two consecutive requests shows its pins block
    unchanged for exactly one turn, and the first shows no streak yet."""
    first, second = str(first), str(second)
    if "GODEL_NATIVE_ITER_PIN" not in second:
        return 0
    if "(unchanged for" in first:
        return 0
    if "(unchanged for 1 turn)" not in second or "(unchanged for 2" in second:
        return 0
    return 1
