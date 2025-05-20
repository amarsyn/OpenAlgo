import requests

#client_id = "1106598724"
access_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzUwMjE4MDg5LCJ0b2tlbkNvbnN1bWVyVHlwZSI6IlNFTEYiLCJ3ZWJob29rVXJsIjoiIiwiZGhhbkNsaWVudElkIjoiMTEwNjU5ODcyNCJ9.9GcBlUZiYnWm5FbPtS5Aejqlk1yeyTFOYZRZzXVSTcgU3CmkCoJ9dGWEw4FbIQ-TMGwOB5n3aeHv2P-nRxp9hQ"

headers = {
    "access-token": access_token
}

url = "https://api.dhan.co/v2/profile"

response = requests.get(url, headers=headers)

if response.status_code == 200:
    print("✅ Dhan Profile Response:")
    print(response.json())
else:
    print(f"❌ Error {response.status_code}: {response.text}")