
# EV CAN - PCAN Gateway (Windows-first MVP)

A local desktop gateway that reads CAN frames from a PEAK PCAN adapter, decodes them using the project's BMS DBC file, and forwards frame batches to the existing Render API.

## Important compatibility notes

This package matches the current EV-CAN-Dashboard API contract:

- `POST /api/ingest-can` with JSON body `{"frames": [...]}`.
- `POST /api/ingest-status` for gateway heartbeat.
- Both requests use `Authorization: Bearer <CAN_INGEST_TOKEN>`.
- Each frame includes `timestamp`, `can_id`, `numeric_id`, `dlc`, `data`, `extended`, `message_name`, `decoded`, and `decode_status`.

Default Render URL: `https://ev-can-dashboard-2d8e.onrender.com`

The gateway downloads the BMS DBC file from the same AWS API URL currently used by the dashboard backend. It does not include the confidential DBC file in this package.

## Prerequisites

1. Windows 10/11, 64-bit.
2. PEAK PCAN-USB adapter.
3. Install the official PEAK PCAN driver / PCAN-Basic runtime for Windows. The Python `python-can` package does not install the hardware driver.
4. Python 3.10 or newer (Python 3.12 recommended).
5. Access to the Render API and a valid CAN ingest token configured on the Render service.

## Run from source

1. Connect the PCAN-USB adapter to the computer and install the PEAK driver.
2. Extract the ZIP.
3. Double-click `run_windows.bat`.
4. In the Gateway window:
   - Render base URL: leave the default.
   - CAN ingest token: enter the token configured in Render.
   - PCAN channel: `PCAN_USBBUS1` (change only if your adapter uses another channel).
   - Bitrate: `500000` (must match the vehicle CAN bus).
5. Click **Start Gateway**.
6. Check the activity log for DBC loaded, PCAN opened, CAN frames, and `Render accepted ... frame(s)`.

The gateway opens the CAN bus in listen-only mode. It does not transmit CAN messages.

## Build a Windows executable

After successfully running `run_windows.bat` once, double-click `build_windows.bat`. The executable output will be under:

`dist/EV-CAN-PCAN-Gateway/`

This build is not code-signed. Windows SmartScreen may show a warning. Test the folder build on a clean Windows machine with the PEAK driver installed before distributing it.

## Troubleshooting

- **PCAN channel / hardware invalid**: install or repair PEAK PCAN-Basic driver, reconnect adapter, verify the channel in PEAK tools, and restart the gateway.
- **No frames received**: verify the CAN bus is powered, correct bitrate, CAN wiring, and bus activity. A connected adapter does not guarantee active traffic.
- **401 Unauthorized**: the token entered in the gateway does not match `CAN_INGEST_TOKEN` configured on Render.
- **DBC download failed**: check internet access and whether the AWS DBC API and temporary download URL are available.
- **Render slow to respond**: the free Render instance may sleep when idle; first requests can take 50 seconds or longer.

## Security and multi-user limitation

This is a Windows-first development MVP for validating one gateway against the current backend. It asks the operator to enter the token at runtime and does not save it in the application.

**Do not publish or distribute a shared Render ingest token.** The current backend uses one shared `CAN_INGEST_TOKEN` and one shared in-memory CAN frame buffer. That is not suitable for multiple independent users. Before public multi-user distribution, the backend must be upgraded with user/device pairing, per-device credentials, session-isolated data, token revocation, and appropriate rate limits. This gateway alone does not create those backend capabilities.

## macOS and Linux

The Python source is portable in principle, but this package is not yet tested or packaged for macOS/Linux. Each OS requires its matching PEAK PCAN-Basic driver/library and a separate build/test cycle.
