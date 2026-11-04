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
import threading
import queue
import json

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



# Set CLOUD_MODE=true in Render. The Render server must never try to open
# the physical PCAN USB adapter attached to the user's laptop.
CLOUD_MODE = os.getenv("CLOUD_MODE", "false").strip().lower() in {"1", "true", "yes"}
last_gateway_heartbeat = None

can_status = {
    "connected": False,
    "message": "Waiting for local PCAN gateway" if CLOUD_MODE else "Waiting for CAN reader to start"
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
    """Read CAN frames from the local PCAN adapter and forward them to Render."""
    global can_status

    render_url = os.getenv("RENDER_CAN_INGEST_URL", "").rstrip("/")
    ingest_token = os.getenv("CAN_INGEST_TOKEN", "")

    outgoing_frames = queue.Queue(maxsize=5000)
    stop_sender = threading.Event()
    heartbeat_thread = None

    def send_status_heartbeat():
        """Tell Render that the local PCAN adapter is still connected."""
        endpoint = f"{render_url}/api/ingest-status"
        headers = {"Authorization": f"Bearer {ingest_token}"}
        with requests.Session() as session:
            while not stop_sender.is_set():
                # Stop refreshing Render's connection heartbeat after the
                # local PCAN reader has detected a disconnect.
                if not can_status.get("connected", False):
                    stop_sender.wait(5)
                    continue
                try:
                    response = session.post(endpoint, headers=headers, timeout=5)
                    if not response.ok:
                        print("HEARTBEAT DEBUG")
                        print("URL:", endpoint)
                        print("STATUS:", response.status_code)
                        print("RESPONSE HEADERS:", dict(response.headers))
                        print("RESPONSE BODY:", repr(response.text))
                except requests.RequestException as error:
                    print(f"Render heartbeat network error: {error}")
                stop_sender.wait(5)

    
    def send_frames_to_render():
        """Forward batches of CAN frames to Render."""

        if not render_url or not ingest_token:
            print("Render forwarding disabled.")
            return

        endpoint = f"{render_url}/api/ingest-can"
        headers = {"Authorization": f"Bearer {ingest_token}"}

        BATCH_SIZE = 50
        BATCH_WAIT = 0.05

        with requests.Session() as session:
            while not stop_sender.is_set() or not outgoing_frames.empty():

                batch = []

                try:
                    first_frame = outgoing_frames.get(timeout=0.5)
                    batch.append(first_frame)
                except queue.Empty:
                    continue

                # Collect additional frames for this batch.
                while len(batch) < BATCH_SIZE:
                    try:
                        batch.append(outgoing_frames.get_nowait())
                    except queue.Empty:
                        break

                try:
                    safe_frames = json.loads(
                        json.dumps(batch, default=str)
                    )

                    response = session.post(
                        endpoint,
                        json={"frames": safe_frames},
                        headers=headers,
                        timeout=15
                    )

                    response.raise_for_status()

                    print(
                        f"Render accepted {len(batch)} frames."
                    )

                except requests.RequestException as error:
                    print(f"Render batch forwarding error: {error}")

                except (TypeError, ValueError) as error:
                    print(f"Frame JSON conversion error: {error}")

                finally:
                    for _ in batch:
                        outgoing_frames.task_done()


    if render_url and ingest_token:
        sender_thread = threading.Thread(
            target=send_frames_to_render,
            daemon=True
        )
        sender_thread.start()
    else:
        print(
            "Render forwarding disabled. Set "
            "RENDER_CAN_INGEST_URL and CAN_INGEST_TOKEN in this terminal."
        )


    try:
        with can.Bus(
            interface="pcan",
            channel="PCAN_USBBUS1",
            bitrate=500000,
            listen_only=True
        ) as bus:
            # The adapter has opened successfully. We will also monitor
            # incoming traffic so an unplugged/failed adapter is not shown
            # as connected forever.
            last_frame_received_at = time.time()
            with data_lock:
                can_status = {
                    "connected": True,
                    "message": "PCAN connected. Listening for frames."
                }
            print("PCAN connected. Listening for CAN frames...")

            # Start a separate heartbeat only after the PCAN adapter opens.
            if render_url and ingest_token:
                heartbeat_thread = threading.Thread(
                    target=send_status_heartbeat,
                    daemon=True
                )
                heartbeat_thread.start()

            last_snapshot_time = time.time()

            while not stop_event.is_set():
                current_time = time.time()

                with recording_lock:
                    should_snapshot = (
                        recording_active
                        and current_time - last_snapshot_time >= snapshot_interval
                    )

                if should_snapshot:
                    create_signal_snapshot()
                    last_snapshot_time = current_time

                msg = bus.recv(timeout=0.1)
                if msg is None:
                    # No traffic for a while can mean the PCAN adapter was
                    # unplugged or the CAN connection was lost. Mark the
                    # dashboard disconnected after 3 seconds without frames.
                    if time.time() - last_frame_received_at >= 3.0:
                        with data_lock:
                            # Remove old live frames so the dashboard cannot
                            # continue displaying values from before disconnect.
                            frames.clear()
                            can_status = {
                                "connected": False,
                                "message": (
                                    "No CAN frames received for 3 seconds. "
                                    "Check the PCAN adapter and CAN bus."
                                )
                            }
                    continue

                last_frame_received_at = time.time()
                with data_lock:
                    can_status = {
                        "connected": True,
                        "message": "PCAN connected. Receiving CAN frames."
                    }

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

                # Update the local dashboard's live frame buffer.
                with data_lock:
                    frames.appendleft(frame)
                    unique_can_ids.add(frame["can_id"])

                # Preserve the existing local recording behavior.
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

                # Queue a copy for cloud forwarding without blocking CAN reads.
                if render_url and ingest_token:
                    try:
                        outgoing_frames.put_nowait(frame.copy())
                    except queue.Full:
                        print("Render forwarding queue full; this frame was skipped.")

                print(
                    f"CAN ID: {frame['can_id']} | "
                    f"Message: {frame['message_name']} | "
                    f"Decode: {frame['decode_status']} | "
                    f"Data: {frame['data']}"
                )

    except Exception as error:
        with data_lock:
            # Clear stale live frames when the PCAN bus raises a disconnect error.
            frames.clear()
            can_status = {
                "connected": False,
                "message": f"PCAN disconnected/unavailable: {error}"
            }
        print(f"PCAN disconnected/unavailable: {error}")
    finally:
        stop_sender.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)


@app.on_event("startup")
def start_reader():
    global reader_thread
    load_dbc()

    # Render is a receiver only. The physical PCAN reader runs on the laptop.
    if CLOUD_MODE:
        print("Cloud mode enabled: waiting for frames from the local PCAN gateway.")
        return

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

    status_snapshot = dict(can_status)
    if CLOUD_MODE:
        heartbeat_is_fresh = (
            last_gateway_heartbeat is not None
            and time.time() - last_gateway_heartbeat <= 15
        )
        status_snapshot["connected"] = heartbeat_is_fresh
        if not heartbeat_is_fresh:
            status_snapshot["message"] = (
                "PCAN adapter disconnected. No recent heartbeat from the local gateway."
            )
            # Do not expose old gateway frames after its heartbeat expires.
            current_frames = []
            current_ids = []

    # The dashboard builds its live BMS cards from this frame list.
    # Returning an empty list while disconnected clears stale signal cards.
    if not status_snapshot.get("connected", False):
        current_frames = []

    return {
        "status": status_snapshot,
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
    """Receive CAN frames and store them for live display and cloud recording."""
    global can_status, last_gateway_heartbeat

    expected_token = os.getenv("CAN_INGEST_TOKEN", "")
    supplied_token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization
        else ""
    )

    if not expected_token or not secrets.compare_digest(
        supplied_token, expected_token
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if isinstance(payload.get("frames"), list):
        incoming_frames = payload["frames"]
    elif isinstance(payload.get("frame"), dict):
        incoming_frames = [payload["frame"]]
    else:
        raise HTTPException(
            status_code=400,
            detail="Expected a 'frame' object or a 'frames' list"
        )

    if not incoming_frames:
        raise HTTPException(status_code=400, detail="No CAN frames supplied")

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

    for index, frame in enumerate(incoming_frames):
        if not isinstance(frame, dict):
            raise HTTPException(
                status_code=400,
                detail=f"Frame at index {index} is not an object"
            )

        missing_fields = required_fields.difference(frame.keys())
        if missing_fields:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Frame at index {index} is missing required fields: "
                    f"{', '.join(sorted(missing_fields))}"
                )
            )

    # Update live dashboard data.
    with data_lock:
        for frame in incoming_frames:
            frames.appendleft(frame)
            unique_can_ids.add(frame["can_id"])

        last_gateway_heartbeat = time.time()
        can_status = {
            "connected": True,
            "message": (
                f"PCAN connected on local laptop; receiving "
                f"{len(incoming_frames)} CAN frame(s)."
            )
        }

    # Store incoming frames and decoded signals during cloud recording.
    with recording_lock:
        if recording_active:
            for frame in incoming_frames:
                recorded_frames.append(frame.copy())

                decoded_signals = frame.get("decoded", [])

                if isinstance(decoded_signals, list):
                    for signal in decoded_signals:
                        if not isinstance(signal, dict):
                            continue

                        signal_name = signal.get("name")
                        if signal_name is None:
                            continue

                        latest_signal_values[signal_name] = {
                            "value": signal.get("value"),
                            "can_id": frame["can_id"]
                        }

    # Generate a signal-history snapshot for CSV export.
    create_signal_snapshot()

    
    # Save incoming frames when cloud recording is active.
    with recording_lock:
        if recording_active:
            for frame in incoming_frames:
                recorded_frames.append(frame.copy())

                for signal in frame.get("decoded", []):
                    signal_name = signal.get("name")

                    if signal_name is not None:
                        latest_signal_values[signal_name] = {
                            "value": signal.get("value"),
                            "can_id": frame["can_id"],
                            "message_name": frame["message_name"],
                            "timestamp": frame["timestamp"]
                        }

    # Create a signal snapshot for CSV export.
    create_signal_snapshot()


    return {
        "ok": True,
        "accepted": len(incoming_frames)
    }

@app.post("/api/ingest-status")
def ingest_gateway_status(
    authorization: str | None = Header(default=None)
):
    """Receive a heartbeat from the local PCAN gateway."""
    global can_status, last_gateway_heartbeat

    expected_token = os.getenv("CAN_INGEST_TOKEN", "")

    supplied_token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization
        else ""
    )

    if not expected_token or not secrets.compare_digest(
        supplied_token, expected_token
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    with data_lock:
        last_gateway_heartbeat = time.time()
        can_status = {
            "connected": True,
            "message": "Local PCAN gateway is connected."
        }

    return {
        "ok": True,
        "message": "Gateway heartbeat received."
    }

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