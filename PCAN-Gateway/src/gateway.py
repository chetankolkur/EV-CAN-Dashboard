import json
import queue
import threading
import keyring
import time
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timezone

import can
import cantools
import requests

DBC_API_URL = "https://ke76ricmk3.execute-api.ap-south-1.amazonaws.com/prod/documents?path=version1&file=bms.dbc"
DEFAULT_RENDER_URL = "https://ev-can-dashboard-2d8e.onrender.com"
BATCH_SIZE = 50
BATCH_INTERVAL = 0.05
NO_FRAME_TIMEOUT = 3.0


class PCANGateway:
    def __init__(self, log_callback, status_callback):
        self.log = log_callback
        self.set_status = status_callback
        self.stop_event = threading.Event()
        self.bus = None
        self.db = None
        self.sender_thread = None
        self.heartbeat_thread = None
        self.reader_thread = None
        self.outgoing = queue.Queue(maxsize=5000)
        self.session = requests.Session()
        self.render_url = ""
        self.token = ""
        self.channel = "PCAN_USBBUS1"
        self.bitrate = 500000
        self.last_frame_time = 0.0
        self.connected = False

    def load_dbc(self):
        self.log("Downloading the configured BMS DBC file...")
        response = self.session.get(DBC_API_URL, timeout=25)
        response.raise_for_status()
        api_data = response.json()
        files = api_data.get("files", [])
        if not files or not files[0].get("url"):
            raise RuntimeError("DBC API did not return a download URL.")
        dbc_response = self.session.get(files[0]["url"], timeout=35)
        dbc_response.raise_for_status()
        self.db = cantools.database.load_string(
            dbc_response.content.decode("utf-8-sig"),
            database_format="dbc"
        )
        self.log(
            f"DBC loaded: {len(self.db.messages)} messages, "
            f"{sum(len(m.signals) for m in self.db.messages)} signals."
        )

    def decode(self, arbitration_id, data, is_extended):
        result = {
            "message_name": "Not found in DBC",
            "decoded": [],
            "decode_status": "unmatched"
        }
        if self.db is None:
            result.update(message_name="DBC unavailable", decode_status="dbc_unavailable")
            return result
        try:
            message = self.db.get_message_by_frame_id(arbitration_id)
            if bool(message.is_extended_frame) != bool(is_extended):
                result.update(message_name="CAN format mismatch", decode_status="format_mismatch")
                return result
            values = message.decode(bytes(data), decode_choices=True, scaling=True)
            result["message_name"] = message.name
            result["decoded"] = [
                {
                    "name": signal.name,
                    "value": values[signal.name],
                    "unit": signal.unit or ""
                }
                for signal in message.signals
                if signal.name in values and values[signal.name] is not None
            ]
            result["decode_status"] = "decoded" if result["decoded"] else "no_signals"
        except KeyError:
            pass
        except Exception as exc:
            result.update(message_name="Decode error", decode_status=f"error: {type(exc).__name__}")
        return result

    def start(self, render_url, token, channel, bitrate):
        if self.reader_thread and self.reader_thread.is_alive():
            raise RuntimeError("Gateway is already running.")
        if not render_url.startswith("https://"):
            raise ValueError("Render URL must start with https://")
        if not token.strip():
            raise ValueError("Enter the CAN ingest token.")
        self.render_url = render_url.rstrip("/")
        self.token = token.strip()
        self.channel = channel.strip()
        self.bitrate = int(bitrate)
        self.stop_event.clear()
        self.outgoing = queue.Queue(maxsize=5000)
        self.reader_thread = threading.Thread(target=self._run, daemon=True)
        self.reader_thread.start()

    def stop(self):
        self.stop_event.set()
        try:
            if self.bus is not None:
                self.bus.shutdown()
        except Exception:
            pass
        self.set_status("Stopping gateway...")

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _send_batches(self):
        endpoint = f"{self.render_url}/api/ingest-can"
        while not self.stop_event.is_set() or not self.outgoing.empty():
            batch = []
            try:
                batch.append(self.outgoing.get(timeout=0.4))
            except queue.Empty:
                continue
            while len(batch) < BATCH_SIZE:
                try:
                    batch.append(self.outgoing.get_nowait())
                except queue.Empty:
                    break
            try:
                safe_batch = make_json_safe(batch)

                response = self.session.post(
                        endpoint,
                        json={"frames": safe_batch},
                        headers=self._headers(),
                        timeout=15
                   )
                response.raise_for_status()
                self.log(f"Render accepted {len(batch)} frame(s).")
            except requests.RequestException as exc:
                self.log(f"Frame forwarding failed: {exc}")
            finally:
                for _ in batch:
                    self.outgoing.task_done()

    def _send_heartbeats(self):
        endpoint = f"{self.render_url}/api/ingest-status"
        while not self.stop_event.is_set():
            # Match the dashboard's existing rule: no received frames for
            # 3 seconds is treated as a disconnected/no-traffic state.
            if self.connected and time.time() - self.last_frame_time < NO_FRAME_TIMEOUT:
                try:
                    response = self.session.post(
                        endpoint, headers=self._headers(), timeout=8
                    )
                    response.raise_for_status()
                except requests.RequestException as exc:
                    self.log(f"Heartbeat failed: {exc}")
            self.stop_event.wait(5)

    def _run(self):
        try:
            self.load_dbc()
            self.log(f"Opening {self.channel} at {self.bitrate} bit/s...")
            self.bus = can.Bus(
                interface="pcan",
                channel=self.channel,
                bitrate=self.bitrate,
                listen_only=True
            )
            self.connected = True
            self.last_frame_time = time.time()
            self.set_status("PCAN adapter opened. Waiting for CAN traffic...")
            self.log("PCAN adapter opened successfully (listen-only).")

            self.sender_thread = threading.Thread(target=self._send_batches, daemon=True)
            self.heartbeat_thread = threading.Thread(target=self._send_heartbeats, daemon=True)
            self.sender_thread.start()
            self.heartbeat_thread.start()

            while not self.stop_event.is_set():
                try:
                    msg = self.bus.recv(timeout=0.2)
                except Exception as exc:
                    raise RuntimeError(f"PCAN read error: {exc}") from exc
                if msg is None:
                    if time.time() - self.last_frame_time >= NO_FRAME_TIMEOUT:
                        self.set_status("No CAN frames received for 3 seconds. Check bus activity.")
                    continue

                self.last_frame_time = time.time()
                decoded = self.decode(msg.arbitration_id, msg.data, msg.is_extended_id)
                frame = {
                    "timestamp": msg.timestamp or time.time(),
                    "can_id": f"0x{msg.arbitration_id:X}",
                    "numeric_id": msg.arbitration_id,
                    "dlc": msg.dlc,
                    "data": msg.data.hex(" ").upper(),
                    "extended": bool(msg.is_extended_id),
                    **decoded
                }
                try:
                    self.outgoing.put_nowait(frame)
                except queue.Full:
                    self.log("Forwarding queue is full; frame skipped.")
                self.set_status(
                    f"Connected | Last frame {frame['can_id']} | {frame['decode_status']}"
                )
                self.log(
                    f"{frame['can_id']} | {frame['message_name']} | "
                    f"{frame['decode_status']} | {frame['data']}"
                )
        except Exception as exc:
            self.connected = False
            self.set_status("Disconnected / error")
            self.log(f"ERROR: {exc}")
            self.log("Check PCAN-Basic driver, adapter, channel, bitrate, and DBC access.")
        finally:
            self.connected = False
            self.stop_event.set()
            try:
                if self.bus is not None:
                    self.bus.shutdown()
            except Exception:
                pass
            self.bus = None
            self.set_status("Gateway stopped")
            self.log("Gateway stopped.")

def make_json_safe(data):
    """Convert decoded CAN data into JSON-compatible Python values."""

    if isinstance(data, dict):
        return {
            key: make_json_safe(value)
            for key, value in data.items()
        }

    if isinstance(data, (list, tuple)):
        return [make_json_safe(item) for item in data]

    if data is None or isinstance(data, (str, int, float, bool)):
        return data

    # Handle cantools NamedSignalValue and similar objects.
    if hasattr(data, "value"):
        return make_json_safe(data.value)

    return str(data)


class GatewayWindow:
    def __init__(self, root):
        self.root = root
        root.title("EV CAN - PCAN Gateway")
        root.geometry("850x620")
        root.minsize(720, 500)

        self.status_var = tk.StringVar(value="Not connected")
        self.url_var = tk.StringVar(value=DEFAULT_RENDER_URL)

        saved_token = keyring.get_password(
                "EV-PCAN-Gateway",
                "CAN_INGEST_TOKEN"
            )

        self.token_var = tk.StringVar(value=saved_token or "")

        self.channel_var = tk.StringVar(value="PCAN_USBBUS1")
        self.bitrate_var = tk.StringVar(value="500000")

        outer = ttk.Frame(root, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="EV CAN - PCAN Gateway", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Connect a local PEAK PCAN adapter and securely forward CAN frames to your Render dashboard."
        ).pack(anchor="w", pady=(2, 12))

        form = ttk.LabelFrame(outer, text="Connection settings", padding=10)
        form.pack(fill="x")
        self._field(form, "Render base URL", self.url_var, 0)
        self._field(form, "CAN ingest token", self.token_var, 1, show="*")
        self._field(form, "PCAN channel", self.channel_var, 2)
        self._field(form, "Bitrate", self.bitrate_var, 3)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=10)
        self.start_button = ttk.Button(controls, text="Start Gateway", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Stop Gateway", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(controls, textvariable=self.status_var).pack(side="left", padx=14)

        log_frame = ttk.LabelFrame(outer, text="Gateway activity", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=18, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        note = (
            "Security: the token is held in memory only and is not saved by this app. "
            "Do not share your token or package it into a public download."
        )
        ttk.Label(outer, text=note, wraplength=800).pack(anchor="w", pady=(8, 0))

        self.gateway = PCANGateway(self.log, self.set_status)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _field(self, parent, label, variable, row, show=None):
        ttk.Label(parent, text=label, width=20).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=variable, show=show)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        parent.columnconfigure(1, weight=1)

    def log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, self._append_log, f"[{stamp}] {message}")

    def _append_log(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_status(self, message):
        self.root.after(0, self.status_var.set, message)

    def start(self):
        try:
            token = self.token_var.get().strip()

            if not token:
                raise ValueError("Enter the CAN ingest token.")

            # Save token securely in Windows Credential Manager
            keyring.set_password(
                "EV-PCAN-Gateway",
                "CAN_INGEST_TOKEN",
                token
            )

            self.gateway.start(
                self.url_var.get(),
                token,
                self.channel_var.get(),
                self.bitrate_var.get()
            )

            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")

            self.log("Starting gateway...")
            self.log("Token saved securely for future launches.")

        except Exception as exc:
            messagebox.showerror("Cannot start gateway", str(exc))

    def stop(self):
        self.gateway.stop()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def close(self):
        self.gateway.stop()
        self.root.after(300, self.root.destroy)


if __name__ == "__main__":
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    GatewayWindow(root)
    root.mainloop()
