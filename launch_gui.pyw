"""Double-click to open the desktop GUI without a console window on Windows."""
from pathlib import Path

from codex_bench.config import default_config_path
from codex_bench.gui import main

raise SystemExit(main(["--config", str(default_config_path(Path(__file__).resolve().parent))]))
