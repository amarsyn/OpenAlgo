from openalgo import api

# Initialize the API client
client = api(api_key='5939519c42f6a0811a7bdb4cf2e1b6ea3e315bd6824a0d6f45c8c46beaa3b4ee', host='http://127.0.0.1:5000')

# Fetch historical data for BHEL
df = client.history(
    symbol="BHEL",
    exchange="NSE",
    interval="5m",
    start_date="2024-11-01",
    end_date="2024-12-31"
)

# Display the fetched data
print(df)

# print(type(df))
# print(df.shape)
# print(df.index)

# print(df.head())
# print(df.columns)
