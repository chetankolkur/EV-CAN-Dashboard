from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from threading import Thread, Lock, Event
from collections import deque
import time
import requests
import csv  # exporting in .csv formate
import io
import os
import secrets

from fastapi import Header, HTTPException

import can
import cantools

app = FastAPI(title="EV CAN Monitoring System")

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "Frontend"

#confidential DBC file 
DBC_API_URL = "https://ke76ricmk3.execute-api.ap-south-1.amazonaws.com/prod/documents?path=version1&file=bms.dbc"

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

frames = deque(maxlen=200)
unique_can_ids = set()
data_lock = Lock()
stop_event = Event()
reader_thread = None

# Recording state — separate from the live dashboard buffer
recording_active = False
recorded_frames = []
recording_lock = Lock()
recording_started_at = None

# Stores the latest actual value and source CAN ID for each signal.
latest_signal_values = {}
signal_history = []
snapshot_interval = 1.0  # Create one snapshot every second

def create_signal_snapshot():
    """Save a snapshot of the latest known signal values."""
    snapshot_time = time.time()

    with recording_lock:
        if not recording_active or not latest_signal_values:
            return

        snapshot = {
            "timestamp": snapshot_time
        }

        for signal_name, signal_info in latest_signal_values.items():
            snapshot[signal_name] = signal_info.get("value")
            snapshot[f"{signal_name}_CAN_ID"] = signal_info.get("can_id")

        signal_history.append(snapshot)



can_status = {
    "connected": False,
    "message": "Waiting for CAN reader to start"
}

dbc_database = None
dbc_status = {
    "loaded": False,
    "message": "DBC has not been checked",
    "message_count": 0,
    "signal_count": 0
}



def load_dbc():
    """Download and load the DBC file using the mentor's AWS API."""
    global dbc_database, dbc_status

    try:
        # Step 1: Request DBC file information from the AWS API.
        response = requests.get(DBC_API_URL, timeout=20)
        response.raise_for_status()
        api_data = response.json()

        # Step 2: Get the temporary download URL.
        files = api_data.get("files", [])
        if not files or not files[0].get("url"):
            raise ValueError("The API response does not contain a DBC download URL")

        download_url = files[0]["url"]

        # Step 3: Download the actual DBC file.
        dbc_response = requests.get(download_url, timeout=30)
        dbc_response.raise_for_status()

        # Step 4: Parse the downloaded DBC content.
        database = cantools.database.load_string(
            dbc_response.content.decode("utf-8-sig"),
            database_format="dbc"
        )

        # Step 5: Store the loaded database and its statistics.
        dbc_database = database
        dbc_status = {
            "loaded": True,
            "message": "DBC loaded successfully from AWS API",
            "message_count": len(database.messages),
            "signal_count": sum(
                len(message.signals) for message in database.messages
            )
        }

        print(
            f"DBC loaded: {dbc_status['message_count']} messages, "
            f"{dbc_status['signal_count']} signals."
        )

    except Exception as error:
        dbc_database = None
        dbc_status = {
            "loaded": False,
            "message": f"Could not load DBC: {type(error).__name__}",
            "message_count": 0,
            "signal_count": 0
        }
        print(dbc_status["message"])



def decode_frame(arbitration_id, data, is_extended_id):
    """Decode raw bytes using the matching message definition in the DBC."""
    result = {
        "message_name": "Not found in DBC",
        "decoded": [],
        "decode_status": "unmatched"
    }

    if dbc_database is None:
        result["message_name"] = "DBC unavailable"
        result["decode_status"] = "dbc_unavailable"
        return result

    try:
        message = dbc_database.get_message_by_frame_id(arbitration_id)

        # A DBC message must also use the same standard/extended CAN format.
        if bool(message.is_extended_frame) != bool(is_extended_id):
            result["message_name"] = "CAN format mismatch"
            result["decode_status"] = "format_mismatch"
            return result

        decoded_values = message.decode(bytes(data), decode_choices=True, scaling=True)
        result["message_name"] = message.name
        result["decoded"] = [
            {
                "name": signal.name,
                "value": value,
                "unit": signal.unit or ""
            }
            for signal in message.signals
            if (value := decoded_values.get(signal.name)) is not None
        ]
        result["decode_status"] = "decoded" if result["decoded"] else "no_signals"
        return result

    except KeyError:
        return result
    except Exception as error:
        result["message_name"] = "Decode error"
        result["decode_status"] = f"error: {type(error).__name__}"
        return result


def read_can_frames():
    """Read local PCAN frames and forward them to Render."""
    import os
    import queue
    import threading
    import requests

    global can_status

    # Configure these environment variables on your laptop.
    render_url = os.getenv("RENDER_CAN_INGEST_URL", "").rstrip("/")
    ingest_token = os.getenv("CAN_INGEST_TOKEN", "")

    # Queue frames so network requests don't block CAN reading.
    outgoing_frames = queue.Queue(maxsize=1000)
    stop_sender = threading.Event()

    def send_frames_to_render():
        """Send CAN frames to the cloud in the background."""
        if not render_url or not ingest_token:
            print(
                "Render forwarding disabled. "
                "Configure RENDER_CAN_INGEST_URL and CAN_INGEST_TOKEN."
            )
            return

        endpoint = f"{render_url}/api/ingest-can"
        headers = {"Authorization": f"Bearer {ingest_token}"}

        while not stop_sender.is_set():
            try:
                frame = outgoing_frames.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                response = requests.post(
                    endpoint,
                    json={"frame": frame},
                    headers=headers,
                    timeout=3
                )
                response.raise_for_status()
            except requests.RequestException as error:
                print(f"Render forwarding error: {error}")
            finally:
                outgoing_frames.task_done()

    sender_thread = threading.Thread(
        target=send_frames_to_render,
        daemon=True
    )
    sender_thread.start()

    try:
        with can.Bus(
            interface="pcan",
            channel="PCAN_USBBUS1",
            bitrate=500000,
            listen_only=True
        ) as bus:
            can_status = {
                "connected": True,
                "message": "PCAN connected. Listening for frames."
            }
            print("PCAN connected. Listening for CAN frames...")

            last_snapshot_time = time.time()

            while not stop_event.is_set():
                current_time = time.time()

                with recording_lock:
                    should_snapshot = (
                        recording_active
                        and current_time - last_snapshot_time
                        >= snapshot_interval
                    )

                if should_snapshot:
                    create_signal_snapshot()
                    last_snapshot_time = current_time

                msg = bus.recv(timeout=0.1)

                if msg is None:
                    continue

                decoded = decode_frame(
                    msg.arbitration_id,
                    msg.data,
                    msg.is_extended_id
                )

                frame = {
                    "timestamp": msg.timestamp or time.time(),
                    "can_id": f"0x{msg.arbitration_id:X}",
                    "numeric_id": msg.arbitration_id,
                    "dlc": msg.dlc,
                    "data": msg.data.hex(" ").upper(),
                    "extended": msg.is_extended_id,
                    **decoded
                }

                # Preserve the existing local dashboard buffer.
                with data_lock:
                    frames.appendleft(frame)
                    unique_can_ids.add(frame["can_id"])

                # Preserve the existing recording and signal tracking.
                with recording_lock:
                    if recording_active:
                        recorded_frames.append(frame.copy())

                        for signal in frame.get("decoded", []):
                            signal_name = signal["name"]

                            latest_signal_values[signal_name] = {
                                "value": signal.get("value"),
                                "can_id": frame["can_id"],
                                "message_name": frame["message_name"],
                                "timestamp": frame["timestamp"]
                            }

                # Forward a copy to the background sender.
                if render_url and ingest_token:
                    try:
                        outgoing_frames.put_nowait(frame.copy())
                    except queue.Full:
                        print(
                            "Render forwarding queue full; "
                            "this frame was skipped."
                        )

                print(
                    f"CAN ID: {frame['can_id']} | "
                    f"Message: {frame['message_name']} | "
                    f"Decode: {frame['decode_status']} | "
                    f"Data: {frame['data']}"
                )

    except Exception as error:
        can_status = {
            "connected": False,
            "message": f"PCAN unavailable: {error}"
        }
        print(f"PCAN unavailable: {error}")




@app.on_event("startup")
def start_reader():
    global reader_thread
    load_dbc()
    stop_event.clear()
    reader_thread = Thread(target=read_can_frames, daemon=True)
    reader_thread.start()


@app.on_event("shutdown")
def shutdown_reader():
    stop_event.set()
    if reader_thread is not None:
        reader_thread.join(timeout=2)


@app.get("/")
def home():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/can-data")
def get_can_data():
    with data_lock:
        current_frames = list(frames)
        current_ids = sorted(
            unique_can_ids,
            key=lambda can_id: int(can_id, 16)
        )

    return {
        "status": can_status,
        "dbc": dbc_status,
        "total_frames": len(current_frames),
        "total_unique_can_ids": len(current_ids),
        "unique_can_ids": current_ids,
        "decoded_frames": sum(
            1 for frame in current_frames
            if frame["decode_status"] == "decoded"
        ),
        "frames": current_frames
    }



@app.post("/api/ingest-can")
def ingest_can_frame(
    payload: dict,
    authorization: str | None = Header(default=None)
):
    global can_status

    # 1. Verify the secret token
    expected_token = os.getenv("CAN_INGEST_TOKEN", "")
    supplied_token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization
        else ""
    )

    if not expected_token or not secrets.compare_digest(
        supplied_token, expected_token
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )

    # 2. Validate the incoming CAN frame
    frame = payload.get("frame")

    if not isinstance(frame, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid CAN frame"
        )

    required_fields = {
        "timestamp",
        "can_id",
        "numeric_id",
        "dlc",
        "data",
        "extended",
        "message_name",
        "decode_status"
    }

    if not required_fields.issubset(frame):
        raise HTTPException(
            status_code=400,
            detail="Incomplete CAN frame"
        )

    # 3. Store the frame and update dashboard status
    with data_lock:
        frames.appendleft(frame)
        unique_can_ids.add(frame["can_id"])

        can_status = {
            "connected": True,
            "message": "Receiving CAN frames from local PCAN."
        }

    return {"ok": True}


@app.post("/api/recording/start")
def start_recording():
    global recording_active
    global recorded_frames
    global recording_started_at
    global latest_signal_values
    global signal_history

    with recording_lock:
        if recording_active:
            return {
                "success": False,
                "message": "Recording is already active."
            }

        # Clear data from the previous recording
        recorded_frames = []
        latest_signal_values = {}
        signal_history = []

        # Start a new recording
        recording_started_at = time.time()
        recording_active = True

    return {
        "success": True,
        "message": "Recording started.",
        "started_at": recording_started_at
    }


@app.post("/api/recording/stop")
def stop_recording():
    global recording_active

    with recording_lock:
        if not recording_active:
            return {
                "success": False,
                "message": "Recording is already stopped."
            }

        # Save the final signal snapshot before stopping
        if latest_signal_values:
            snapshot = {
                "timestamp": time.time()
            }

            for signal_name, signal_info in latest_signal_values.items():
                snapshot[signal_name] = signal_info.get("value")
                snapshot[f"{signal_name}_CAN_ID"] = (
                    signal_info.get("can_id")
                )

            signal_history.append(snapshot)

        recording_active = False
        total_recorded = len(recorded_frames)
        total_snapshots = len(signal_history)

    return {
        "success": True,
        "message": "Recording stopped.",
        "total_recorded": total_recorded,
        "total_snapshots": total_snapshots
    }


@app.get("/api/recording/status")
def get_recording_status():
    with recording_lock:
        return {
            "recording": recording_active,
            "total_recorded": len(recorded_frames),
            "started_at": recording_started_at
        }


@app.get("/api/recording/export")
def export_recording():
    from fastapi.responses import Response

    # Copy recorded snapshots safely
    with recording_lock:
        export_snapshots = [
            snapshot.copy()
            for snapshot in signal_history
        ]

    if not export_snapshots:
        raise HTTPException(
            status_code=400,
            detail=(
                "No signal snapshots available. Start recording "
                "and receive CAN data first."
            )
        )

    # Collect signal names, excluding timestamps and CAN ID metadata
    signal_names = sorted({
        key
        for snapshot in export_snapshots
        for key in snapshot
        if key != "timestamp" and not key.endswith("_CAN_ID")
    })

    columns = ["timestamp"] + signal_names

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()

    # Carry forward the latest recorded value for each signal
    latest_values = {}

    for snapshot in export_snapshots:
        latest_values.update(snapshot)

        timestamp = snapshot.get("timestamp")

        row = {
            "timestamp": (
                time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(timestamp)
                )
                if timestamp is not None
                else "N/A"
            )
        }

        for signal_name in signal_names:
            row[signal_name] = latest_values.get(
                signal_name, "N/A"
            )

        writer.writerow(row)

    csv_content = "\ufeff" + output.getvalue()
    output.close()

    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                'attachment; filename="can_signal_history.csv"'
        }
    )