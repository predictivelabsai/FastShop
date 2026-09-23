"""Sandbox renewal worker. Configure scheduling only after provider acceptance tests.

The orchestration lives in ``app.subscription_renewals.process_due`` so the bundled
scheduler and this CLI share one implementation.
"""

import argparse

from app.subscription_renewals import process_due


def process(*, limit=50, **kwargs):
    return process_due(limit=limit, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    print(process(limit=args.limit))


if __name__ == "__main__":
    main()
