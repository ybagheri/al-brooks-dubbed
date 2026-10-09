"""Module entry point: ``python -m src.main``.

The real argument parsing lives in :mod:`src.cli`; this module only exists so
that the documented ``python -m src.main`` command works.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
