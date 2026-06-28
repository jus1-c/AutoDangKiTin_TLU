"""
AutoDangKiTin TLU - Main Entry Point
A tool for automatic course registration at Thang Long University.

Usage:
    python main.py          # Launch GUI mode (default)
    python main.py --tui    # Launch TUI mode (terminal)
    python main.py --help   # Show help
"""

import sys
import argparse


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="AutoDangKiTin TLU - Auto course registration tool for Thang Long University",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py          Launch GUI mode (default)
    python main.py --tui    Launch TUI mode (terminal)
        """
    )
    parser.add_argument(
        '--tui', 
        action='store_true',
        help='Run in Terminal User Interface (TUI) mode'
    )
    parser.add_argument(
        '--version', 
        action='version', 
        version='AutoDangKiTin TLU v2.0.0'
    )
    return parser.parse_args()


def main():
    """Main entry point - dispatch to GUI or TUI based on arguments."""
    args = parse_args()
    
    if args.tui:
        # Run TUI mode
        from main_tui import run_tui
        run_tui()
    else:
        # Run GUI mode (default)
        from main_gui import run_gui
        run_gui()


if __name__ == "__main__":
    main()