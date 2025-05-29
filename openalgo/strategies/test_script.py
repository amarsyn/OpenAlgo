from openalgo import api

client = api.initialize()

exchange = "NSE"  # Or the relevant exchange for your symbols

response = client.history(
    symbol="NSE:TECHNOE",
    exchange="NSE",
    interval="5m",
    start_date="2025-05-27",
    end_date="2025-05-28"
)
print(response)
