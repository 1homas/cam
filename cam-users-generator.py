#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Generate CSV file with N Meraki IAM (IdP) users for bulk import testing.

Creates a CSV file with configurable number of users for testing
cam-users.py's --create-from-csv at scale.

Usage:
    cam-users-generator.py --count 1000000 --output users.csv
    cam-users-generator.py --count 1000 --prefix employee --password Sup3rSecret!
"""

import csv
import signal
import sys
from pathlib import Path


def generate_users_csv(count: int, output_file: Path, send_password: bool = False, prefix: str = "user", password: str | None = None) -> None:
    """Generate CSV file with N users."""
    print(f"Generating {count:,} users...")

    with output_file.open("w", newline="") as f:
        writer = csv.writer(f)

        # Header (matches cam-users.py --create-from-csv columns)
        writer.writerow(["email", "displayName", "password", "sendPassword"])

        # Generate users in batches for progress reporting
        batch_size = 10000
        for i in range(1, count + 1):
            email = f"{prefix}{i:07d}@example.com"
            display_name = f"{prefix.capitalize()} {i:07d}"
            user_password = password if password else f"Pass{i:07d}!"

            writer.writerow([
                email,
                display_name,
                user_password,
                "true" if send_password else "false",
            ])

            if i % batch_size == 0:
                print(f"  Generated {i:,} users ({i/count*100:.1f}%)")

    size_mb = output_file.stat().st_size / (1024 * 1024)
    print(f"\nCreated: {output_file}")
    print(f"Size: {size_mb:.1f} MB")
    print(f"Users: {count:,}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate CSV with N Meraki IAM (IdP) users")
    parser.add_argument("--count", "-c", type=int, default=1_000_000, help="Number of users to generate (default: 1,000,000)")
    parser.add_argument("--output", "-o", type=Path, default="million-users.csv", help="Output CSV file (default: million-users.csv)")
    parser.add_argument("--send-password", action="store_true", help="Set sendPassword=true for generated users (default: false)")
    parser.add_argument("--prefix", "-p", default="user", help="Username prefix for email and displayName (default: user)")
    parser.add_argument("--password", default=None, help="Fixed password for all generated users (default: unique per-user, Pass{n:07d}!)")

    args = parser.parse_args()

    def _sigterm_to_keyboard_interrupt(*_args):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _sigterm_to_keyboard_interrupt)

    try:
        generate_users_csv(args.count, args.output, args.send_password, args.prefix, args.password)
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...", file=sys.stderr)
        sys.exit(130)
