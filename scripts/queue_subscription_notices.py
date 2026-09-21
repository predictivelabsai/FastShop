"""Queue upcoming sandbox renewal notices; schedule alongside the mail worker."""

import argparse

from app.subscription_notifications import queue_upcoming


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-ahead", type=int, default=3, choices=range(1, 31))
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    print({"queued": queue_upcoming(days_ahead=args.days_ahead, limit=args.limit)})


if __name__ == "__main__":
    main()
