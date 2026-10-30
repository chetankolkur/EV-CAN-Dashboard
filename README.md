
# EV CAN Monitoring Dashboard

A Python-based dashboard for monitoring and decoding Controller Area Network (CAN) data in Electric Vehicles (EVs). The project uses FastAPI for the backend, a web-based frontend, and DBC files to interpret CAN messages into meaningful vehicle signals.

## Features

- Monitor CAN bus messages from a compatible PCAN USB interface.
- Decode CAN messages using DBC files.
- Retrieve DBC data from an AWS-hosted API.
- Display CAN monitoring information through a web interface.
- Record CAN data and export it to CSV for further analysis.
- Use a Python backend with a browser-based frontend.

## Technology Stack

### Backend
- **Python** — Core programming language.
- **FastAPI** — Builds the backend API and handles HTTP requests.
- **Uvicorn** — Runs the FastAPI application as an ASGI server.
- **python-can** — Interfaces with CAN hardware and reads CAN bus messages.
- **cantools** — Loads DBC files and decodes CAN messages into signals.
- **requests** — Sends HTTP requests to retrieve DBC data from an API.

### Frontend
- **HTML** — Structures the dashboard interface.
- **CSS** — Styles the dashboard.
- **JavaScript** — Handles frontend interactions and dynamic updates.

### Hardware and Data
- Compatible PCAN USB interface and required drivers for hardware-based CAN monitoring.
- DBC (Database CAN) files for decoding CAN messages.
- AWS-hosted API for DBC data retrieval.

## Project Structure

```text
EV_CAN_Reader/
├── app.py
├── requirements.txt
├── .gitignore
└── Frontend/
    ├── index.html
    ├── Style.css
    └── Script.js
```

Additional Python utility files may exist in the local project.

## Prerequisites

- Python 3.12 or a compatible Python version.
- pip (Python package installer).
- Git (optional, for version control).
- PCAN USB hardware and its drivers for live CAN monitoring.
- Access to the configured DBC data API.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/chetankolkur/EV-CAN-Dashboard.git
cd EV-CAN-Dashboard
```

### 2. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

The dependencies are listed in `requirements.txt`.

## Run the Application

From the project root, activate your virtual environment and run:

```powershell
uvicorn app:app --reload
```

Open the local address printed by Uvicorn in your browser, usually:

http://127.0.0.1:8000

If the frontend is not served at the root URL, check the routes configured in `app.py` for the correct dashboard URL.

## How It Works

1. The application connects to the configured CAN data source.
2. CAN messages are received through the supported interface.
3. DBC definitions map CAN message identifiers and signal bits to meaningful signal names and values.
4. The backend processes the decoded information for the dashboard.
5. Users can monitor data and record or export it to CSV.

## Deployment Notes

The application can be hosted on a cloud platform, but a cloud-hosted service cannot directly access a PCAN USB device physically connected to a developer's computer.

Live CAN monitoring in a cloud deployment requires an appropriate remote data-ingestion solution or a separate local device that forwards CAN data securely.

Ensure that any API URLs, credentials, and other configuration values are handled securely. Do not commit secrets or API keys to GitHub.

## Future Improvements

- Add support for additional CAN interfaces.
- Improve telemetry visualization and filtering.
- Add historical data analysis.
- Implement secure remote CAN data ingestion.
- Expand automated testing and deployment.

## Author

**Chetan Kolkur**

GitHub: https://github.com/chetankolkur
