"""`python -m gateway` runs the `safeguard` command."""

import sys

from gateway.offline import force_offline

# Before the command line, and so the engine, is imported (review M7).
force_offline()

from gateway.cli import main  # noqa: E402  (after force_offline on purpose)

sys.exit(main())
