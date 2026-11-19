"""Allow running `python -m trace_memory <command>`."""

from __future__ import annotations

import sys

from trace_memory.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
