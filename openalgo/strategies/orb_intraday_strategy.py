# Opening Range Breakout (ORB) Intraday Strategy
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import os
import requests
from openalgo import api
from dotenv import load_dotenv
from sqlalchemy import create_engine, MetaData, Table
import json
load_dotenv()

# Configuration
api_key = '5939519c42f6a0811a7bdb4cf2e1b6ea3e315bd6824a0d6f45c8c46beaa3b4ee'
symbols = ["HDFCBANK", "TECHM", "TRENT", "ADANIPOWER","BHARTI"]
exchange = "NSE"
interval = "1m"
start_date = "2025-03-01"
end_date = datetime.now().strftime("%Y-%m-%d")

client = api(api_key=api_key, host='http://127.0.0.1:5000')

# Telegram Alert
TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        requests.post(url, data=payload)

def log_trade_to_analyzer(symbol, direction, entry_price, exit_price, entry_time, exit_time, request_payload, response_data):
    engine = create_engine('sqlite:///../db/openalgo.db')
    metadata = MetaData()
    analyzer_logs = Table('analyzer_logs', metadata, autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(analyzer_logs.insert(), [{
            'instrument': symbol,
            'strategy': "ORB",
            'signal': direction,
            'entry_price': float(entry_price),
            'exit_price': float(exit_price),
            'entry_time': entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            'exit_time': exit_time.strftime("%Y-%m-%d %H:%M:%S"),
            'run_mode': "analyze",
            'api_type': "history",
            'request_data': json.dumps(request_payload),
            'response_data': json.dumps(response_data)
        }])

def analyze_orb(symbol):
    url = "http://127.0.0.1:5000/api/v1/history"
    payload = {
        "apikey": os.getenv("OPENALGO_API_KEY"),
        "symbol": symbol,
        "exchange": exchange,
        "interval": interval,
        "start_date": start_date,
        "end_date": end_date
    }

    response = requests.post(url, json=payload).json()
    print(f"Response for {symbol}:", response)

    if not isinstance(response, dict) or "data" not in response:
        send_telegram(f"⚠️ Error fetching data for {symbol}: {response}")
        return pd.DataFrame(), []

    df = pd.DataFrame(response["data"])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
    df.set_index('datetime', inplace=True)
    print(f"⏱ Data range for {symbol}: {df.index.min()} to {df.index.max()}")
    df_day = df
    print(f"✅ Loaded {len(df_day)} candles for {symbol}")

    if df.empty:
        print(f"No data returned for {symbol} on {start_date}")
        send_telegram(f"⚠️ No data returned for {symbol} on {start_date}")
        return pd.DataFrame(), []

    orb_range = df_day.between_time("09:15", "09:30")
    high = orb_range['high'].max()
    low = orb_range['low'].min()

    buffer = 0.001  # 0.1%
    entry_long = high * (1 + buffer)
    entry_short = low * (1 - buffer)

    sl_pct = 0.005
    target_pct = 0.01

    trade_log = []
    in_trade = False
    direction = None
    entry_price = None
    entry_time = None

    for i, row in df_day.iterrows():
        price = row['close']
        if not in_trade:
            if price > entry_long:
                entry_price = price
                stop_loss = entry_price * (1 - sl_pct)
                target = entry_price * (1 + target_pct)
                direction = "BUY"
                entry_time = i
                in_trade = True
                send_telegram(f"{symbol} LONG Entry at {entry_price:.2f} on {entry_time}")
            elif price < entry_short:
                entry_price = price
                stop_loss = entry_price * (1 + sl_pct)
                target = entry_price * (1 - target_pct)
                direction = "SELL"
                entry_time = i
                in_trade = True
                send_telegram(f"{symbol} SHORT Entry at {entry_price:.2f} on {entry_time}")
        else:
            if (direction == "BUY" and (price >= target or price <= stop_loss)) or \
               (direction == "SELL" and (price <= target or price >= stop_loss)):
                exit_price = price
                exit_time = i
                trade_log.append({
                    "symbol": symbol,
                    "direction": direction,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "entry_time": entry_time,
                    "exit_time": exit_time
                })
                result = f"{symbol} {direction} Exit at {exit_price:.2f} on {exit_time}"
                send_telegram(result)
                log_trade_to_analyzer(symbol, direction, entry_price, exit_price, entry_time, exit_time, payload, response)
                in_trade = False
                break

    if not trade_log:
        send_telegram(f"{symbol} - No trade executed on {start_date}")

    return df_day, trade_log

# Run Strategy
for symbol in symbols:
    df, trade_log = analyze_orb(symbol)
    if not df.empty and trade_log:
        plot_trade(df, trade_log, symbol)
    else:
        print(f"Skipping plot for {symbol} due to missing data or trade.")
