"""Double-click to open the desktop GUI without a console window on Windows."""
from pathlib import Path

from codex_bench.gui import main

raise SystemExit(main(["--config", str(Path(__file__).resolve().with_name("config.json"))]))
