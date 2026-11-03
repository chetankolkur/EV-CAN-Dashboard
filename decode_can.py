
from pathlib import Path
import cantools

# Path to your confidential DBC file
DBC_PATH = Path(r"C:\Users\test\Downloads\bms.dbc")

# Previously captured CAN frame
CAN_ID = 0x18C928F4
RAW_DATA = bytes.fromhex("0C FE 0C FE 0C FE 0C FE")

try:
    # Load the DBC definitions
    database = cantools.database.load_file(str(DBC_PATH))

    # Find the message definition for this CAN ID
    message = database.get_message_by_frame_id(CAN_ID)

    # Decode the raw bytes into signal values
    decoded_signals = message.decode(RAW_DATA)

    print("DBC loaded successfully!")
    print(f"CAN ID: 0x{CAN_ID:X}")
    print(f"Message name: {message.name}")
    print("\nDecoded signals:")

    for signal_name, value in decoded_signals.items():
        print(f"{signal_name}: {value}")

except KeyError:
    print("This CAN ID was not found in the DBC.")

except Exception as error:
    print("Decoding failed:", type(error).__name__)
    print(error)
