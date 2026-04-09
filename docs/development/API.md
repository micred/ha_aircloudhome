# AirCloud Home API Reference

This document describes the AirCloud Home cloud API endpoints used by this integration.

**Base URLs:**

- Japan / legacy: `https://api-kuma.aircloudhome.com`
- Europe / global: `https://api-global-prod.aircloudhome.com`

> **Tested hardware:** API behaviour documented here was verified against a **Hitachi RAS-X40L2**. Other **room air conditioners (RAC)** connected via AirCloud Home should be compatible. Packaged air conditioners (PAC) use a separate API and are out of scope for this integration.

## API Migration Status

The integration was originally built around the Japan / legacy API shape and is being migrated to the Europe / global API.

### What is confirmed

- `POST /iam/auth/sign-in` works against the global base URL and returns access and refresh tokens.
- `GET /iam/family-account/v2/groups` appears to remain part of the account model.
- `GET /iam/user/v2/who-am-i` appears to expose account metadata such as `familyId`.
- The Home Assistant integration now sends mobile-app style headers (`Accept`, `Content-Type`, `User-Agent: okhttp/4.2.2`) while calling the global API.

### Current blocker

The old Japan flow assumed this sequence:

1. Sign in
2. Fetch family groups
3. Fetch devices with `GET /rac/ownership/groups/{familyId}/idu-list`
4. Poll device state from that REST response

That flow does not translate directly to the Europe / global API.

For a tested Europe account, the following call returned `403 FORBIDDEN`:

```text
GET https://api-global-prod.aircloudhome.com/rac/ownership/groups/cloudIds/{familyId}
```

Response body:

```json
{
  "error": "FORBIDDEN",
  "message": "Access Denied"
}
```

### Practical consequence

This is not just a hostname migration. Authentication works, but device discovery and state retrieval are not yet confirmed to be REST-compatible with the older Japan integration.

The strongest current inference is that the Europe / global app uses a different ownership or notification flow, likely involving websocket-based state updates rather than the old REST `idu-list` polling model.

Treat the REST device-list endpoints below as:

- confirmed for the Japan / legacy API
- unconfirmed or incompatible for the Europe / global API unless otherwise stated

### Current integration behavior

The code currently:

- uses the global base URL in [`custom_components/aircloudhome/api/client.py`](../../custom_components/aircloudhome/api/client.py)
- attempts `who-am-i` as a fallback source for `familyId`
- attempts `GET /rac/ownership/groups/cloudIds/{familyId}` for global discovery
- logs config-flow exceptions explicitly so migration failures are visible in Home Assistant logs

### Next migration work

The remaining migration challenge is to discover and implement the Europe / global device discovery and state channel, not merely rename REST endpoints.

## Authentication

### Sign In

Authenticates a user and returns JWT access and refresh tokens.

- **URL:** `POST /iam/auth/sign-in`
- **Headers:**
  - `Content-Type: application/json`

**Request body:**

```json
{
  "email": "user@example.com",
  "password": "your-password"
}
```

**Response — 200 OK:**

```json
{
  "token": "<access_token>",
  "refreshToken": "<refresh_token>",
  "newUser": false,
  "errorState": "NONE",
  "access_token_expires_in": 1209600000,
  "refresh_token_expires_in": 7776000000
}
```

| Field | Description |
| ----- | ----------- |
| `token` | JWT access token (valid for ~14 days: `access_token_expires_in` ms) |
| `refreshToken` | JWT refresh token (valid for ~90 days: `refresh_token_expires_in` ms) |
| `newUser` | Intended to indicate a new user, but observed to return `true` for existing users as well — exact semantics unclear |
| `errorState` | `"NONE"` on success |

**Response — 401 Unauthorized:** Invalid email or password.

---

### Token Refresh

Obtains a new access token using a valid refresh token.

- **URL:** `POST /iam/auth/refresh`
- **Headers:**
  - `Content-Type: application/json`
  - `Authorization: Bearer <refresh_token>`
  - `isRefreshToken: true`
- **Body:** none

**Response — 200 OK:**

```json
{
  "token": "<new_access_token>",
  "refreshToken": "<new_refresh_token>",
  "errorState": "NONE",
  "access_token_expires_in": 1209600000
}
```

---

## Device Information

Device data is structured in **family groups**. A single account may belong to multiple family groups, each containing one or more AC units (IDUs). Fetch the family group list first, then query each group for its devices.

### List Family Groups

Returns all family groups the authenticated user belongs to.

- **URL:** `GET /iam/family-account/v2/groups`
- **Headers:**
  - `Authorization: Bearer <access_token>`

**Response — 200 OK:**

```json
{
  "message": "",
  "result": [
    {
      "familyId": 100001,
      "familyName": "My Home",
      "createdBy": "user@example.com",
      "role": {
        "id": 1,
        "name": "OWNER",
        "level": 1
      },
      "pictureData": null
    }
  ]
}
```

| Field | Description |
| ----- | ----------- |
| `familyId` | Unique identifier for the family group — used in subsequent API calls |
| `role.name` | User's role in the group (`OWNER`, etc.) |

---

### List IDUs in Family Group

Returns all AC units (indoor units / IDUs) registered to a family group.

- **URL:** `GET /rac/ownership/groups/{familyId}/idu-list`
- **Headers:**
  - `Authorization: Bearer <access_token>`

**Response — 200 OK:**

```json
[
  {
    "userId": "000000",
    "serialNumber": "XXXX-XXXX-XXXX",
    "model": "HITACHI",
    "id": 10001,
    "vendorThingId": "JCH-xxxxxxxx",
    "name": "Living Room",
    "roomTemperature": 21.5,
    "mode": "HEATING",
    "iduTemperature": 21.0,
    "humidity": 50,
    "power": "ON",
    "relativeTemperature": 0.0,
    "fanSpeed": "AUTO",
    "fanSwing": "AUTO",
    "updatedAt": 1700000000000,
    "lastOnlineUpdatedAt": 1700000000000,
    "racTypeId": 6,
    "iduFrostWash": false,
    "specialOperation": false,
    "criticalError": false,
    "zoneId": "Asia/Tokyo",
    "scheduleType": "SCHEDULE_DISABLED",
    "online": true
  }
]
```

**Key fields:**

| Field | Values | Description |
| ----- | ------ | ----------- |
| `id` | integer | Device ID — used as `racId` in control commands |
| `vendorThingId` | string | Vendor-assigned device identifier (contains partial MAC address) |
| `model` | string | Manufacturer name (e.g. `"HITACHI"`) |
| `serialNumber` | string | Unit serial number (observed to be `"XXXX-XXXX-XXXX"` in real data — may not be uniquely set per device) |
| `name` | string | User-defined room name (may contain non-ASCII) |
| `online` | boolean | Whether the device is currently reachable |
| `power` | `"ON"` \| `"OFF"` | Current power state |
| `mode` | see below | Current operating mode |
| `fanSpeed` | see below | Current fan speed |
| `fanSwing` | see below | Current swing direction |
| `iduTemperature` | float | Target temperature (°C, 0.5° increments) |
| `roomTemperature` | float | Current room temperature (°C) |
| `humidity` | integer | Target humidity setting (40–60%) |

**`mode` values:**

| API value | Meaning (on app) |
| --------- | ------- |
| `HEATING` | Heating (暖房) |
| `COOLING` | Cooling (冷房) |
| `FAN` | Fan (送風) |
| `DRY` | Dry (除湿) |
| `DRY_COOL` | Dry Cool (涼快) |
| `AUTO` | Auto (自動) |
| `UNKNOWN` | Unknown / other |

**`fanSpeed` values:**

- `AUTO` (Auto/自動 on app)
- `LV1` (1 on app)
- `LV2` (2 on app)
- `LV3` (3 on app)
- `LV4` (4 on app)
- `LV5` (5 on app)

**`fanSwing` values:**

| API value | Meaning |
| --------- | ------- |
| `AUTO` | Auto swing |
| `OFF` | Swing off |
| `VERTICAL` | Vertical sweep |
| `HORIZONTAL` | Horizontal sweep |
| `BOTH` | All directions |

---

## Device Control

### Send Control Command

Updates the operating state of an AC unit. All five required fields must be included; omitting any required field returns `400 Bad Request`. For fields you are not changing, pass the current device value.

- **URL:** `PUT /rac/basic-idu-control/general-control-command/{racId}?familyId={familyId}`
- **Headers:**
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`

**Request body:**

```json
{
  "power": "ON",
  "mode": "HEATING",
  "fanSpeed": "AUTO",
  "fanSwing": "OFF",
  "iduTemperature": 21.5
}
```

**Required fields:**

| Field | Values | Description |
| ----- | ------ | ----------- |
| `power` | `"ON"` \| `"OFF"` | Power state |
| `mode` | `HEATING` \| `COOLING` \| `FAN` \| `DRY` \| `DRY_COOL` \| `AUTO` \| `UNKNOWN` | Operating mode |
| `fanSpeed` | `AUTO` \| `LV1` \| `LV2` \| `LV3` \| `LV4` \| `LV5` | Fan speed |
| `fanSwing` | `AUTO` \| `OFF` \| `VERTICAL` \| `HORIZONTAL` \| `BOTH` | Swing direction |
| `iduTemperature` | float (16–32, step 0.5) | Target temperature (°C) |

**Optional fields:**

| Field | Values | Description |
| ----- | ------ | ----------- |
| `humidity` | integer (40–60, step 5) | Target humidity (%). **Only valid when `mode` is `DRY` or `DRY_COOL`.** Including this field in any other mode returns `400 Bad Request`. |

**Response — 200 OK:**

```json
{
  "commandId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "thingId": "th.xxxxxxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

The response confirms the command was accepted; actual device state change is asynchronous. Poll the IDU list to confirm the updated state.
