import pandas as pd
import json

# Load your Dhan symbol CSV
df = pd.read_csv("api-scrip-master (1).csv")

# Filter for NSE EQ stocks
df = df[
    (df["SEM_EXM_EXCH_ID"] == "NSE") &
    (df["SEM_SERIES"] == "EQ") &
    (df["SEM_EXCH_INSTRUMENT_TYPE"] == "EQUITY")
]

# Create symbol map
symbol_map = {
    row["SM_SYMBOL_NAME"].strip().upper(): str(row["SEM_SMST_SECURITY_ID"]).strip()
    for _, row in df.iterrows()
    if pd.notna(row["SM_SYMBOL_NAME"]) and pd.notna(row["SEM_SMST_SECURITY_ID"])
}

# Save to JSON file
with open("master_symbol_map.json", "w") as f:
    json.dump(symbol_map, f, indent=2)

print("✅ master_symbol_map.json generated with", len(symbol_map), "symbols.")
