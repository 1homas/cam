#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""
Generates a series of MAC addresses based on count, OUI, and random station IDs.

This script provides a command-line interface to generate MAC addresses.
Users can specify the number of MAC addresses to generate (--count or -c),
an Organizationally Unique Identifier (--oui or -o), and case preference
(--upper or --lower). If no OUI is provided, a random OUI will be generated
for each MAC address. The default count is 1 and output is lowercase.

Each generated MAC address is printed on a new line to standard output.

Examples:
  # Generate 5 MAC addresses with a specific OUI (e.g., Cisco's OUI)
  ./mac-generator.py -c 5 -o c0:ff:ee

  # Generate 1 MAC address with a specific OUI (default count is 1)
  ./mac-generator.py -o 00:11:22

  # Generate 3 MAC addresses with random OUIs
  ./mac-generator.py -c 3

  # Generate 10 MAC addresses in uppercase
  ./mac-generator.py -c 10 --upper

  # Generate MAC addresses with specific OUI in uppercase
  ./mac-generator.py -c 5 -o c0:ff:ee --upper

  # Generate a single MAC address with a random OUI (default count and OUI)
  ./mac-generator.py
"""

import argparse
import random
import sys


def generate_random_oui() -> str:
    """Generates a random Organizationally Unique Identifier (OUI).

    Returns:
        A 6-character hexadecimal string representing a random OUI (e.g., "a1b2c3").
    """
    random_oui_bytes = [random.randint(0x00, 0xFF) for _ in range(3)]
    return "".join(f"{b:02x}" for b in random_oui_bytes)


def generate_mac_address(oui: str, uppercase: bool = False) -> str:
    """Generates a MAC address with a given OUI and a random station ID.

    Args:
        oui: The Organizationally Unique Identifier (OUI) as a 6-character hexadecimal string
             (e.g., "c0ffee" or "c0:ff:ee").
        uppercase: If True, return MAC address in uppercase; otherwise lowercase.

    Returns:
        A formatted MAC address string (e.g., "c0:ff:ee:ba:be:ee" or "C0:FF:EE:BA:BE:EE").

    Raises:
        ValueError: If the provided OUI is not a valid 6-character hexadecimal string.
    """
    oui_clean = oui.replace(":", "").upper()

    if not (len(oui_clean) == 6 and all(c in "0123456789ABCDEF" for c in oui_clean)):
        raise ValueError(f"Invalid OUI format: '{oui}'. OUI must be a 6-character hexadecimal string "
                         f"(e.g., 'c0ffee' or 'c0:ff:ee').")

    station_id_bytes = [random.randint(0x00, 0xFF) for _ in range(3)]
    station_id_str = "".join(f"{b:02x}" for b in station_id_bytes)
    full_mac_hex = oui_clean + station_id_str
    formatted_mac = ":".join(full_mac_hex[i : i + 2] for i in range(0, 12, 2))

    return formatted_mac.upper() if uppercase else formatted_mac.lower()


def main():
    """Main entry point for the MAC address generator."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-c", "--count", type=int, default=1,
                        help="The number of MAC addresses to generate (default: 1).")
    parser.add_argument("-o", "--oui", type=str,
                        help="The Organizationally Unique Identifier (OUI) as a 6-character hexadecimal string "
                             "(e.g., 'c0ffee' or 'c0:ff:ee'). If not provided, a random OUI will be used for each "
                             "MAC address.")
    parser.add_argument("--upper", action="store_true",
                        help="Output MAC addresses in uppercase (default: lowercase).")
    parser.add_argument("--lower", action="store_true",
                        help="Output MAC addresses in lowercase (default).")

    args = parser.parse_args()

    if args.count <= 0:
        print("Error: The 'count' must be a positive integer.", file=sys.stderr)
        sys.exit(1)

    if args.upper and args.lower:
        print("Error: Cannot specify both --upper and --lower.", file=sys.stderr)
        sys.exit(1)

    # Default to lowercase unless --upper is specified
    use_uppercase = args.upper

    try:
        for _ in range(args.count):
            current_oui = generate_random_oui() if args.oui is None else args.oui
            mac = generate_mac_address(current_oui, uppercase=use_uppercase)
            print(mac)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
