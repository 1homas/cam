# CLAUDE.md

## Setup

- Install `uv` and always run Python scripts as PEP 723 with `uv run`

## Documentation

- Update `ReadMe.md` with any new scripts and features
- Update the `<script>.md` specification file with any added features

## Key Files

- `.env` — Meraki Dashboard and Org Credentials (gitignored)
- `*_test.py` — Unit tests
- `*.log` — logs per script
- `references/openapiSpec.json` — Meraki Dashboard OpenAPI spec

## Packages

Use Python packages:

- httpx
- pyyaml
- asyncio

## Output

Output should be in JSON by default with options for YAML, CSV, and pretty printed tables.

## Logging

- `*.log` : Outbound Meraki API requests per script are logged (method, URL, status, size, transfer time, retries) with ISO 8601 timestamps `YYYY-MM-DDTHH:MM:SSZ`

## Testing Guidelines

- **ALWAYS use Test-Driven Development (TDD)** — Red/Green/Refactor cycle is mandatory
- **Red First**: Before making ANY code change
- **Green Second**: Make the minimal fix to pass
  1. Edit the code
  2. Restart/reload the app
  3. Re-test the same feature path
  4. Capture evidence showing GREEN state
- **Never claim success without runtime evidence** — "I changed X" is not proof that X works
- test files are named after their script: `<script>_test.py`
