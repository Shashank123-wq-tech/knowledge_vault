"""Run in a second terminal: python worker.py. No Redis or Celery required."""
import argparse
import logging
import sqlite3
import time

from app.config import database_path
from app.database import initialize
from app.indexing import process_one


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = database_path()
    initialize(path)
    logging.info("Index worker started. Press Ctrl+C to stop.")
    try:
        while True:
            try:
                worked = process_one(path)
            except sqlite3.OperationalError:
                logging.exception("Database temporarily unavailable")
                if args.once:
                    raise
                worked = False
            if args.once:
                break
            if not worked:
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        logging.info("Worker stopped; an interrupted lease can be reclaimed after 60 seconds.")


if __name__ == "__main__":
    main()
