"""Exec one host-selected tool under a parent-owned process lifetime."""
import ctypes
import os
import signal
import sys

if ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0):
    raise SystemExit(75)
if os.getppid() != int(sys.argv[1]):
    raise SystemExit(75)
# argv comes from the controller's immutable tool registry, not model text.
os.execv(sys.argv[2], sys.argv[2:])
