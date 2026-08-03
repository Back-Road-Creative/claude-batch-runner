"""PyInstaller entry point for the Windows build.

PyInstaller freezes a *script*. claude-batch-runner's console entry point is a
module function (``claude-batch-runner = "claude_batch_runner.__main__:main"``
in pyproject.toml), which pip turns into a generated shim at install time --
there is no file on disk to point PyInstaller at. This is that file, and it
does nothing else.

The target module is literally named ``__main__``, so it carries its own
``if __name__ == "__main__"`` block. Importing it as
``claude_batch_runner.__main__`` binds ``__name__`` to that dotted path, not to
``"__main__"``, so the block does not fire and argparse does not run twice.
Only this launcher is ``__main__`` in the frozen exe.
"""

from __future__ import annotations

import sys

from claude_batch_runner.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
