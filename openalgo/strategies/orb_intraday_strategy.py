# Opening Range Breakout (ORB) Intraday Strategy with Enhancements
import pandas as pd
from datetime import datetime, timedelta
import os
import requests
from openalgo import api
from dotenv import load_dotenv
from sqlalchemy import create_engine, MetaData, Table
import json
import signal
import sys
import time
load_dotenv

# Configuration
api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
symbols = ["RELIANCE", "DCI"]
exchange = "NSE"
interval = "1m"
start_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
end_date = datetime.now().strftime("%Y-%m-%d")

LOG_FILE = f"logs/ORB_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/ORB_{datetime.now().strftime('%Y-%m-%d')}.csv"

client = api(api_key=api_key, host='http://127.0.0.1:5000')

# Telegram Alert
TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        response = requests.post(url, data=payload)
        if response.ok:
            print(f"📩 ORB: {message}")
        else:
            print(f"❌ ORB: {response.status_code} {response.text}")

def log_message(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"{timestamp} ORB - {msg}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] ORB {msg}\n")

def log_trade_to_analyzer(symbol, direction, entry_price, exit_price, entry_time, exit_time):
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
            'entry_time': entry_time.strftime("%Y-%m-%d %H:%M"),
            'exit_time': exit_time.strftime("%Y-%m-%d %H:%M"),
            'run_mode': "analyze",
            'api_type': "history",
            'request_data': json.dumps({}),
            'response_data': json.dumps({})
        }])

def analyze_orb(symbol):
    df = client.history(symbol=symbol, exchange=exchange, interval=interval,
            start_date=(datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d'),
            end_date=datetime.now().strftime('%Y-%m-%d'))
    if isinstance(df, dict) and "data" in df:
        df = pd.DataFrame(df["data"])

    if not isinstance(df, pd.DataFrame) or df.empty:
        send_telegram(f"⚠️ No data returned for {symbol}")
        return pd.DataFrame(), []

    log_message(f"{symbol} history returned {len(df)} rows from API.")
    log_message(f"{symbol} columns: {df.columns.tolist()}")

    if not isinstance(df.index, pd.DatetimeIndex):
        if 'time' in df.columns:
            df['datetime'] = pd.to_datetime(df['time'])
            df.set_index('datetime', inplace=True)
        elif 'timestamp' in df.columns:
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
            df.set_index('datetime', inplace=True)
        else:
            send_telegram(f"⚠️ Missing time column in data for {symbol}")
            return pd.DataFrame(), []

    # ORB window disabled — using full session range instead
    high = df['high'].max()
    low = df['low'].min()
    if pd.isna(high) or pd.isna(low):
        send_telegram(f"⚠️ ORB range missing or incomplete for {symbol}")
        log_message(f"{symbol} - ORB data missing between 09:15–09:30")
        log_message(f"{symbol} - Data head:\n{df.head(5)}")
        log_message(f"{symbol} - ORB range rows: {len(orb_range)}")

    # Calculate dynamic ATR-based target (using 14-period ATR on 1m data)
    df['H-L'] = df['high'] - df['low']
    df['H-PC'] = abs(df['high'] - df['close'].shift(1))
    df['L-PC'] = abs(df['low'] - df['close'].shift(1))
    df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    df['ATR'] = df['TR'].rolling(window=14).mean()
    latest_atr = df['ATR'].iloc[-1] if not df['ATR'].isna().all() else 1
    atr_multiplier = 2.5  # can be adjusted
    dynamic_target_buffer = latest_atr * atr_multiplier
    log_message(f"{symbol} ATR: {latest_atr:.2f}, Dynamic Target Buffer: {dynamic_target_buffer:.2f}")

    buffer = 0.001
    entry_long = high * (1 + buffer)
    entry_short = low * (1 - buffer)

    sl_pct = 0.005
    trail_trigger_pct = 0.006
    trailing_sl_pct = 0.003
    partial_exit_pct = 0.004
    partial_exit_size = 0.5  # book 50% at partial

    trade_log = []
    in_trade = False
    direction = None
    entry_price = None
    entry_time = None
    stop_loss = None
    target = None
    trailing_active = False
    partial_booked = False

    for i, row in df.iterrows():
        price = row['close']
        timestamp = i.strftime("%Y-%m-%d %H:%M")

        if not in_trade:
            if price > entry_long:
                entry_price = price
                stop_loss = entry_price * (1 - sl_pct)
                direction = "BUY"
                entry_time = i
                in_trade = True
                partial_booked = False
                trailing_active = False
                send_telegram(f"{symbol} LONG Entry at {entry_price:.2f} on {timestamp}")
            elif price < entry_short:
                entry_price = price
                stop_loss = entry_price * (1 + sl_pct)
                direction = "SELL"
                entry_time = i
                in_trade = True
                partial_booked = False
                trailing_active = False
                send_telegram(f"{symbol} SHORT Entry at {entry_price:.2f} on {timestamp}")

        else:
            if direction == "BUY":
                if not partial_booked and price >= entry_price * (1 + partial_exit_pct):
                    partial_booked = True
                    send_telegram(f"{symbol} Partial exit booked at {price:.2f} (+{partial_exit_pct*100:.1f}%)")
                if not trailing_active and price >= entry_price * (1 + trail_trigger_pct):
                    trailing_active = True
                    send_telegram(f"{symbol} Trailing SL Activated for LONG at {price:.2f}")
                if trailing_active:
                    new_sl = price * (1 - trailing_sl_pct)
                    if new_sl > stop_loss:
                        stop_loss = new_sl
                if price <= stop_loss:
                    exit_price = price
                    exit_time = i
                    send_telegram(f"{symbol} {direction} Exit at {exit_price:.2f} on {exit_time.strftime('%Y-%m-%d %H:%M')}")
                    log_trade_to_analyzer(symbol, direction, entry_price, exit_price, entry_time, exit_time)
                    trade_log.append({
                        'symbol': symbol,
                        'direction': direction,
                        'entry_time': entry_time,
                        'exit_time': exit_time,
                        'entry_price': entry_price,
                        'exit_price': exit_price
                    })
                    in_trade = False
            elif direction == "SELL":
                if not partial_booked and price <= entry_price * (1 - partial_exit_pct):
                    partial_booked = True
                    send_telegram(f"{symbol} Partial exit booked at {price:.2f} (-{partial_exit_pct*100:.1f}%)")
                if not trailing_active and price <= entry_price * (1 - trail_trigger_pct):
                    trailing_active = True
                    send_telegram(f"{symbol} Trailing SL Activated for SHORT at {price:.2f}")
                if trailing_active:
                    new_sl = price * (1 + trailing_sl_pct)
                    if new_sl < stop_loss:
                        stop_loss = new_sl
                if price >= stop_loss:
                    exit_price = price
                    exit_time = i
                    send_telegram(f"{symbol} {direction} Exit at {exit_price:.2f} on {exit_time.strftime('%Y-%m-%d %H:%M')}")
                    log_trade_to_analyzer(symbol, direction, entry_price, exit_price, entry_time, exit_time)
                    in_trade = False

    if not trade_log:
        send_telegram(f"{symbol} - No trade executed on {start_date}")

    return df, trade_log

# =======================
# Graceful Exit
# =======================
def graceful_exit(signum, frame):
    print("Amar's ORB Strategy Graceful shutdown requested... Exiting strategy.")
    send_telegram("🛑 Amar's ORB Strategy stopped gracefully.")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

# =======================
# Main Execution
# =======================
if __name__ == '__main__':
    print("📊 Starting Amar's ORB Strategy...")
    send_telegram("✅ Amar's ORB Strategy started.")
    while True:
        for symbol in symbols:
            df, trade_log = analyze_orb(symbol)
            if not df.empty and trade_log:
                print(f"Completed trade for {symbol}.")
            else:
                print(f"No trade or data for {symbol}.")
        time.sleep(10)
