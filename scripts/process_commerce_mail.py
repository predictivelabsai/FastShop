"""Run from the application environment on a scheduler; prints counts, no secrets."""

import argparse

from app.integrations.commerce_email import process_due_mail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    print(process_due_mail(limit=args.limit))


if __name__ == "__main__":
    main()
