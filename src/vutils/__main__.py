"""Allow `python -m vutils` to run the CLI."""

from .vcli import main

raise SystemExit(main())
