"""Engine-fault detection token + classifier.

This module does NOT fix why the Rockwell engine (LdSdkServer.exe) faults —
that is an SDK-level issue outside our control. It only provides detection and
a recovery hook so callers can restart the engine and retry idempotent operations.
"""
from __future__ import annotations

ENGINE_FAULT_TOKEN = "LgxSrv_E_SERVER_FAULTED"


def is_engine_fault(exc: BaseException) -> bool:
    """Return True if *exc* indicates the Rockwell engine has faulted."""
    return ENGINE_FAULT_TOKEN in str(exc)
