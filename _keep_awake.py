"""Keep Windows awake for the lifetime of THIS process — no system-setting change.

Calls SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED), the same
mechanism media players use to stop the machine sleeping during playback.
The flag is process-scoped and cleared automatically when this process
exits, so it never persists or alters the user's power plan. Run it in the
background for the duration of a long benchmark, then kill it.

Usage:
    python _keep_awake.py
"""
from __future__ import annotations

import sys
import time

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

try:
    import ctypes
    ok = ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    if ok == 0:
        print("SetThreadExecutionState failed", flush=True)
        sys.exit(1)
    print("keep-awake ON (system will not idle-sleep while this runs)", flush=True)
except Exception as e:  # noqa: BLE001
    print(f"keep-awake unavailable: {e}", flush=True)
    sys.exit(1)

try:
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    pass
finally:
    # Clear the flag explicitly (also cleared on process exit).
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass
    print("keep-awake OFF", flush=True)
