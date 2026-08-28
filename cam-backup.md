# CAM Backup

## Task

Create a Python script to make a backup of Cisco Access Manager data using by fetching the data with REST APIs then save the JSON output to different object files.

## Environment Variables

- Source environment variables from a `.env` file
- Use `MERAKI_ORG_ID` in the REST URL path; override per-run with `--org`/`-o`
- Use `MERAKI_DASHBOARD_API_KEY` for the `Authorization: Bearer` token

## Style

- Use Python PEP8 with a 4-space indentation and 120 character line width
- Use Python PEP723
- Put the usage description in the script's `__doc__` variable
- Use functional patterns where possible
- Write Google style docstrings for functions and inline comments for non-obvious code
- Prefer using f-strings

## REST API Endpoints

You may need to perform repeated curl fetches if the list of items is greater than the page size.

- Example response:

      {
        "meta": {
          "totalCount": 7,
          "filteredCount": 7
        },
        "items": [
          {
            "id": "627126248111341637",
            "mac": "c0:ff:ee:ba:be:ee",
            "description": "blah blah blah",
            "status": "Disconnected",
            "hasPrivateMac": false,
            "source": "Provisioned"
          }
        ]
      }

- use `meta.totalCount` for the total number of clients

## Output

- Create a `backups` directory if one does not exist.
- Under `backups`, create a directory for today's date in the format `YYYY-MM-DD`
- Save the output of each downloaded object set to it's own file
- Show status for each endpoint download:
  - ✅ for an HTTP 2xx response (success)
  - ❌ for an HTTP 4xx or 5xx response (error)
  - Status icons displayed at the beginning of each line
- Only use `?perPage=1000` parameter for the clients endpoint (large dataset)
- Show summary at end with success/failure counts and error details
