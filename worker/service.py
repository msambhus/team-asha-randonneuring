#!/usr/bin/env python3
"""Run live polling and scheduled maintenance in one Railway service."""

import threading

from poll_loop import main as poll_main
from scheduler import main as scheduler_main


def main():
    scheduler = threading.Thread(target=scheduler_main, name="scheduler", daemon=True)
    scheduler.start()
    poll_main()


if __name__ == "__main__":
    main()
