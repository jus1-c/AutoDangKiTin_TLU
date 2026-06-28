# -*- coding: utf-8 -*-
"""
AutoDangKiTin TLU - Entry point

Usage:
  python main.py            # launch TUI (default)
  python main.py --help     # show all CLI commands
  python main.py login
  python main.py register --index 0 --index 1
  python main.py sniff --index 0
  python main.py profile list
  ...
"""
import sys

from src.config import Config


def main() -> None:
    Config.ensure_dirs()
    Config.load_settings()

    # No args: launch TUI
    if len(sys.argv) == 1:
        from src.tui.app import run_tui
        run_tui()
        return

    # Otherwise: dispatch to Typer CLI
    from src.cli.app import app
    app()


if __name__ == "__main__":
    main()
