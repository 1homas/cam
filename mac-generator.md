## Task

Create a Python script that generates a series of random MAC addresses based on a count, and optional OUI.

## Style

- Use Python PEP8 with a 4-space indentation and 120 character line width
- Use Python PEP723
- Put the usage description in the script's `__doc__` variable
- Use functional patterns where possible
- Write Google style docstrings for functions and inline comments for non-obvious code
- Prefer using f-strings

### Inputs

- `count`: the number of MAC addresses to generate. Default: 1
- `oui`: the first six digits of the MAC address. Default: a random OUI
- `upper`: flag to use all uppercase hex digits
- `lower`: flag use all lowercase hex digits

### Outputs

MAC addresses, each on a new line.

Example:

```
C0:FF:EE:BA:BE:EE
A0:CE:C8:D3:5B:2B
9C:8E:CD:2D:2C:17
9C:8E:CD:37:DA:13
00:00:00:00:00:01
00:00:00:00:00:02
00:00:00:00:00:03
```
