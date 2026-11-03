
from pathlib import Path
import cantools

DBC_PATH = Path(r"C:\Users\test\Downloads\bms.dbc")

# CAN IDs currently observed on your dashboard.
# These are examples from your earlier output.
observed_ids = {
    0x18C928F4,
    0x18CA28F4,
    0x18CB28F4,
    0x18DF96F8,
    0x18FE28F4,
    0x18FF28F4,
    0x18FF50E6,
    0x18FFE5F4,
    0x8C,
    0x1,
    0x2,
}

try:
    database = cantools.database.load_file(str(DBC_PATH))

    # Match observed IDs with DBC message definitions.
    dbc_ids = {
        message.frame_id
        for message in database.messages
    }

    matched = observed_ids & dbc_ids
    unmatched = observed_ids - dbc_ids

    print("DBC loaded successfully.")
    print("Number of message definitions:", len(database.messages))
    print("Observed CAN IDs checked:", len(observed_ids))
    print("IDs matching the DBC:", len(matched))
    print("IDs not found in the DBC:", len(unmatched))

    print("\nMatching CAN IDs:")
    for can_id in sorted(matched):
        print(f"0x{can_id:X}")

    print("\nUnmatched CAN IDs:")
    for can_id in sorted(unmatched):
        print(f"0x{can_id:X}")

except Exception as error:
    print("Test failed:", type(error).__name__)
    print("Check the DBC path and file format.")
