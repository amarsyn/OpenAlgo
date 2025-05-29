from openalgo import api
from datetime import datetime, timedelta
import httpx

# Initialize API client with host
client = api(api_key='your_api_key_here', host="http://127.0.0.1:5000")

# Date range for historical data
end_date = datetime.now()
start_date = end_date - timedelta(days=20)

# Manually recreate payload for test
payload = {
    "symbol": "SBIN",
    "exchange": "NSE",
    "interval": "5m",
    "start_date": start_date.strftime("%Y-%m-%d"),
    "end_date": end_date.strftime("%Y-%m-%d")
}
headers = {"Authorization": f"Bearer {client.api_key}"}
url = "http://127.0.0.1:5000/history"

# Post request with timeout
try:
    response = httpx.post(url, json=payload, headers=headers, timeout=60.0)
    print("Status:", response.status_code)
    print("Response:", response.text)
except Exception as e:
    print("Error:", e)
