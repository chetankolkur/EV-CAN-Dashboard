
from pathlib import Path
import cantools

DBC_PATH = Path(r"C:\Users\test\Downloads\bms.dbc")

try:
    if not DBC_PATH.is_file():
        print("DBC file not found. Check the path.")
    else:
        database = cantools.database.load_file(str(DBC_PATH))

        print("DBC loaded successfully!")
        print("Total messages:", len(database.messages))

except Exception as error:
    print("DBC loading failed:", type(error).__name__)
    print("Check the path, file format, and file validity.")
