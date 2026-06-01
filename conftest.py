"""Make `import argus` work for pytest regardless of editable-install state.

Same root cause as scripts/postsync.sh: Python 3.13 skips `_*.pth` files, so
hatchling's editable install doesn't actually register the project. Putting
the project root on sys.path here means tests pass under bare `pytest`,
`uv run pytest`, or a fresh-clone-and-go workflow without needing the
bootstrap script first.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
