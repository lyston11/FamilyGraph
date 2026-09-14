"""Ensure the checked-out ``app`` package wins over the shared editable install.

The development ``.venv`` (often symlinked) registers an editable finder that
maps ``app`` to the main checkout.  Importing the main checkout's services
from another worktree silently runs stale code.  Prepending this directory to
``sys.path`` makes the local worktree authoritative; the editable finder is
installed last, so this is sufficient and harmless in the main checkout.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
