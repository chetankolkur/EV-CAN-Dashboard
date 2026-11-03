
import can

print("Starting EV CAN Reader...")

try:
    with can.Bus(
        interface="pcan",
        channel="PCAN_USBBUS1",
        bitrate=500000,
        listen_only=True
    ) as bus:
        print("PCAN connected. Waiting for CAN frames...")
        print("Format: CAN ID | DLC | Raw Data (HEX)")

        while True:
            msg = bus.recv(timeout=1)
            if msg:
                print(
                    f"CAN ID: 0x{msg.arbitration_id:X} | "
                    f"DLC: {msg.dlc} | "
                    f"Raw Data: {msg.data.hex(' ').upper()}"
                )

except KeyboardInterrupt:
    print("\nCAN Reader stopped.")

except Exception as error:
    print(f"Error: {error}")
