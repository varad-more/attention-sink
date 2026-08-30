#!/usr/bin/env python
"""Validate the local pilot protocol, and report anything that has drifted.

Reads only. Says whether the five machine-readable files agree with each other,
whether each one still matches the digest it recorded, and whether the manifest still
describes the files on disk. Exits non-zero if any of that is false, so it is usable
as a gate.

    python scripts/validate_local_protocol.py [--root experiment/pilot]
"""

from __future__ import annotations

import sys

from attention_sink.pilot import main

if __name__ == "__main__":
    raise SystemExit(main([*sys.argv[1:], "validate"]))
