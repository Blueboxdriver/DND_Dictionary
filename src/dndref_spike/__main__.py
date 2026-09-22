from __future__ import annotations

import argparse

from .app import SpikeApp


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the disposable Textual/image spike.")
    parser.add_argument(
        "--images",
        choices=("auto", "off", "kitty", "sixel"),
        default="auto",
        help="image capability policy (default: auto)",
    )
    args = parser.parse_args()
    SpikeApp(image_mode=args.images).run()


if __name__ == "__main__":
    main()
