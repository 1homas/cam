# CAM Guest Purge

Create a PEP 723 python script that finds all old Guest clients older than a specified time duration or 7 days by default where:

- ssid contains the value specified by `--ssid`
- source = "Discovered"
- lastLogin.timestamp <= now - duration
- `status` = `Disconnected`

## Options

Must show these in the `--help` option along with example usage:

| Flag          | Type   | Default | Description                                                                |
| ------------- | ------ | ------- | -------------------------------------------------------------------------- |
| --age         | string | `7d`    | Age as `DDdHHhMMmSSs` or plain seconds (e.g. `7d`, `2d12h`, `30m`, `3600`) |
| --ssid        | string |         | Name of the SSID to match (required, case-insensitive contains)            |
| --dry-run     | flag   | false   | List matching clients without deleting                                     |
| -v, --verbose | flag   | false   | Enable verbose logging (also enabled by `--dry-run`)                       |
| --help        | flag   |         | Show command options and examples                                          |

## Environment Variables

| Variable                   | Description              |
| -------------------------- | ------------------------ |
| `MERAKI_DASHBOARD_API_KEY` | Meraki Dashboard API key |
| `MERAKI_ORG_ID`            | Meraki organization ID   |

Loaded from `.env` via `python-dotenv`.

## Behavior

- **Pagination**: Follows `Link` header `startingAfter` token to fetch all clients (1000 per page)
- **Rate limiting**: Retries on HTTP 429 using `Retry-After` header
- **Batch deletion**: Sends bulkDelete requests in batches of 100 client IDs
- **Logging**: Structured output with ISO 8601 timestamps to stderr

## API Endpoints

GET https://api.meraki.com/api/v1/organizations/$MERAKI_ORG_ID/nac/clients

```json
{
    "id": "627126248111374692",
    "mac": "32:e8:39:21:6e:bd",
    "firstLogin": {
    "node": {
        "id": 0,
        "type": "",
        "group": {
        "id": 0,
        "localeName": ""
        },
        "serial": ""
    },
    "timestamp": "2026-05-18T21:49:28Z"
    },
    "lastLogin": {
    "node": {
        "id": 0,
        "type": "",
        "group": {
        "id": 0,
        "localeName": ""
        },
        "serial": ""
    },
    "location": "Demo",
    "timestamp": "2026-05-23T00:02:29Z"
    },
    "ssid": "AM-Guest",
    "nadName": "68-49-92-37-61-A0:vap2",
    "lanIpAddresses": {
    "ipv4": [
        "192.168.128.3"
    ]
    },
    "status": "Disconnected",
    "hasPrivateMac": true,
    "source": "Discovered",
    "classification": {
    "type": "Mobile",
    "manufacturer": "Apple",
    "os": "iOS"
    }
},
```

POST https://api.meraki.com/api/v1/organizations/$MERAKI_ORG_ID/nac/clients/bulkDelete

## Testing

- Use TDD for all operations
- Always test using `--dry-run` to avoid deleting real clients
- Create test clients to avoid deleting real clients
