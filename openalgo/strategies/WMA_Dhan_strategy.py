# =======================
# Import Dependencies
# =======================
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
import time
import sys
import signal
import requests
import os
import threading
from collections import defaultdict
import csv
import statistics

# ==============================
# Setup and Configuration
# ==============================
os.makedirs("logs", exist_ok=True)

strategy_name = "WMA Dhan Strategy"
with open("symbol_WMA_Dhan.json", "r") as f:
    symbol_map = json.load(f)  # Add more mappings as needed
exchange = "NSE_EQ"
product = "INTRADAY"
quantity = 5
mode = "live"
start_time = "09:19"
end_time = "15:50"
sl_pct = 1  
target_pct = 3
trailing_sl_pct = 0.5
trailing_trigger_pct = 0.8

# Dhan Credentials
DHAN_API_KEY = "1106598724"
DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzUwMzIwMTUxLCJ0b2tlbkNvbnN1bWVyVHlwZSI6IlNFTEYiLCJ3ZWJob29rVXJsIjoiIiwiZGhhbkNsaWVudElkIjoiMTEwNjU5ODcyNCJ9.Yh_bfiEAzcgak6FbcH6lj3jMYkSHqovB5WOVT5TssWTex8z0wlM_3cROZKakDI0p2DjrZATGPKfjarZgJF8zZQ"
DHAN_BASE_URL = "https://api.dhan.co"

HEADERS = {
    "accept": "application/json",
    "access-token": DHAN_ACCESS_TOKEN,
    "Content-Type": "application/json",
    "dhan-client-id": DHAN_API_KEY
}

last_trade_time = defaultdict(lambda: datetime.min)
cooldown_seconds = 300
max_bars = 48
test_days = 90
backtest_end_date = datetime(2024, 11, 22)

LOG_FILE = f"logs/WMA_Dhan_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/WMA_Dhan_{datetime.now().strftime('%Y-%m-%d')}.csv"

TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

# =====================
# Utility Functions
# =====================
def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        requests.post(url, data=payload)

def log_message(msg):
    if mode.lower() in ["backtest", "analyze"]:
        timestamp = backtest_end_date.strftime('%Y-%m-%d') + " (BT)"
    else:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"[{timestamp}] WMA_Dynamic {msg}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] WMA_Dynamic {msg}\n")

def log_trade(symbol, entry_price, exit_price, direction, reason):
    try:
        profit_pct = ((exit_price - entry_price) / entry_price) * 100 if direction == "bullish" else ((entry_price - exit_price) / entry_price) * 100
        profit_pct = round(profit_pct, 2)
    except Exception as e:
        log_message(f"[ERROR] Failed to calculate profit: {e}")
        profit_pct = 0.0
    with open("trade_log.csv", "a") as f:
        f.write(f"{datetime.now()},{symbol},{entry_price},{exit_price},{profit_pct:.2f},{reason}\n")

# =====================
# MACD Crossover Utility
# =====================
def recent_macd_bullish_cross(df, lookback=4):
    macd = df['macd']
    macd_signal = df['macd_signal']
    for i in range(-lookback - 1, -1):
        if macd.iloc[i - 1] < macd_signal.iloc[i - 1] and macd.iloc[i] > macd_signal.iloc[i]:
            return True
    return False

def recent_macd_bearish_cross(df, lookback=4):
    macd = df['macd']
    macd_signal = df['macd_signal']
    for i in range(-lookback - 1, -1):
        if macd.iloc[i - 1] > macd_signal.iloc[i - 1] and macd.iloc[i] < macd_signal.iloc[i]:
            return True
    return False

# =====================
# Data Fetching
# =====================
def fetch_data(symbol):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=test_days)
    # Placeholder: replace with actual data fetch logic
    # The result should be a DataFrame with columns: ['open', 'high', 'low', 'close', 'volume']
    # Simulated dummy DataFrame
    index = pd.date_range(end=end_date, periods=100, freq='5min')
    df = pd.DataFrame({
        'open': [1580]*100,
        'high': [1590]*100,
        'low': [1570]*100,
        'close': [1580]*100,
        'volume': [10000]*100
    }, index=index)

    df['wma'] = ta.wma(df['close'], length=20)
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['vol_ma'] = df['volume'].rolling(20).mean()
    macd_df = ta.macd(df['close'])
    df['macd'] = macd_df['MACD_12_26_9']
    df['macd_signal'] = macd_df['MACDs_12_26_9']
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    return df.dropna()

# =====================
# Trend Detection
# =====================
def detect_trend(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    log_message(
        f"Checking trend — Close: {last['close']}, WMA: {last['wma']}, Prev_WMA: {prev['wma']}, "
        f"RSI: {last['rsi']}, MACD: {last['macd']}, Signal: {last['macd_signal']}, "
        f"Vol: {last['volume']}, Vol_MA: {last['vol_ma']}, ATR: {last['atr']}"
    )
    if last['volume'] < last['vol_ma'] * 0.75:
        log_message(f"Filtered due to weak volume: {last['volume']} < 75% of MA {last['vol_ma']}")
        return None
    if last['vol_ma'] < 10000:
        log_message("⚠️ Very low average volume, skipping for safety.")
        return None
    if last['rsi'] >= 53 and last['macd'] - last['macd_signal'] > 0.01 and last['wma'] > prev['wma']:
        log_message("Valid Bullish Trend Detected.")
        return "bullish"
    elif last['rsi'] <= 47 and last['macd'] - last['macd_signal'] < -0.05 and last['wma'] < prev['wma']:
        log_message("Valid Bearish Trend Detected.")
        return "bearish"
    log_message("No valid trend detected.")
    return None

# =====================
# Trailing SL Logic
# =====================
def monitor_position(symbol, direction, entry_price, sl, target):
    in_position = True
    trail_triggered = False
    trail_target = None
    while in_position:
        time.sleep(60)
        df = fetch_data(symbol)
        if df is None or len(df) < 2:
            continue
        current_price = df['close'].iloc[-1]
        if direction == "bullish":
            if current_price <= sl:
                reason = "Stop Loss Hit"
                exit_price = current_price
                in_position = False
            elif current_price >= target:
                reason = "Target Hit"
                exit_price = current_price
                in_position = False
            elif not trail_triggered and current_price >= entry_price * (1 + trailing_trigger_pct / 100):
                trail_triggered = True
                trail_target = current_price * (1 - trailing_sl_pct / 100)
            elif trail_triggered and current_price <= trail_target:
                reason = "Trailing SL Hit"
                exit_price = current_price
                in_position = False
            elif trail_triggered:
                trail_target = max(trail_target, current_price * (1 - trailing_sl_pct / 100))
        else:
            if current_price >= sl:
                reason = "Stop Loss Hit"
                exit_price = current_price
                in_position = False
            elif current_price <= target:
                reason = "Target Hit"
                exit_price = current_price
                in_position = False
            elif not trail_triggered and current_price <= entry_price * (1 - trailing_trigger_pct / 100):
                trail_triggered = True
                trail_target = current_price * (1 + trailing_sl_pct / 100)
            elif trail_triggered and current_price >= trail_target:
                reason = "Trailing SL Hit"
                exit_price = current_price
                in_position = False
            elif trail_triggered:
                trail_target = min(trail_target, current_price * (1 + trailing_sl_pct / 100))
    log_trade(symbol, entry_price, exit_price, direction, reason)
    send_telegram(f"{symbol} → {reason} at {exit_price:.2f}")

## =====================
# Dhan Order Functions
# =====================
def dhan_place_order(symbol, direction, quantity):
    action = "BUY" if direction == "bullish" else "SELL"
    payload = {
        "transactionType": action,
        "exchangeSegment": exchange,
        "productType": product,
        "orderType": "MARKET",
        "price": 0.0,
        "quantity": quantity,
        "instrumentId": symbol,
        "orderValidity": "DAY"
    }
    try:
        r = requests.post(f"{DHAN_BASE_URL}/orders", headers=HEADERS, json=payload)
        data = r.json()
        if r.status_code == 200 and data.get("status") == "success":
            return {"status": "success", "orderid": data["orderId"]}
        else:
            return {"status": "error", "message": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def dhan_place_sl_order(symbol, direction, entry_price):
    reverse = "SELL" if direction == "bullish" else "BUY"
    sl_price = round(entry_price * (1 + sl_pct / 100), 1) if reverse == "BUY" else round(entry_price * (1 - sl_pct / 100), 1)
    trigger_price = round(sl_price + 0.5, 1) if reverse == "BUY" else round(sl_price - 0.5, 1)
    payload = {
        "transactionType": reverse,
        "exchangeSegment": exchange,
        "productType": product,
        "orderType": "SL-M",
        "triggerPrice": trigger_price,
        "price": 0.0,
        "quantity": quantity,
        "instrumentId": symbol,
        "orderValidity": "DAY"
    }
    try:
        r = requests.post(f"{DHAN_BASE_URL}/orders", headers=HEADERS, json=payload)
        data = r.json()
        if r.status_code == 200 and data.get("status") == "success":
            return {"status": "success", "orderid": data["orderId"]}
        else:
            return {"status": "error", "message": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def dhan_place_target_order(symbol, direction, entry_price):
    reverse = "SELL" if direction == "bullish" else "BUY"
    target_price = round(entry_price * (1 + target_pct / 100), 1) if reverse == "SELL" else round(entry_price * (1 - target_pct / 100), 1)
    payload = {
        "transactionType": reverse,
        "exchangeSegment": exchange,
        "productType": product,
        "orderType": "LIMIT",
        "price": target_price,
        "quantity": quantity,
        "instrumentId": symbol,
        "orderValidity": "DAY"
    }
    try:
        r = requests.post(f"{DHAN_BASE_URL}/orders", headers=HEADERS, json=payload)
        data = r.json()
        if r.status_code == 200 and data.get("status") == "success":
            return {"status": "success", "orderid": data["orderId"]}
        else:
            return {"status": "error", "message": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# =====================
# Main Strategy Execution
# =====================
def run_strategy():
    log_message("Amar's WMA Dynamic Strategy started in LIVE mode.")
    while True:
        try:
            now = datetime.now()
            current_time = now.strftime("%H:%M")

            if current_time < start_time or current_time > end_time:
                log_message("Outside trading window. Sleeping...")
                time.sleep(60)
                continue

            for symbol in symbols:
                log_message(f"Processing {symbol}...")
                if (datetime.now() - last_trade_time[symbol]).total_seconds() < cooldown_seconds:
                    log_message(f"⏳ Skipping {symbol} — cooldown in effect")
                    continue

                df = fetch_data(symbol)
                if df is None or len(df) < 30:
                    continue

                direction = detect_trend(df)
                if not direction:
                    continue

                close_price = df.iloc[-1]['close']
                atr = df.iloc[-1]['atr']

                if direction == "bullish":
                    sl = close_price - 1.25 * atr
                    target = close_price + 2.5 * atr
                else:
                    sl = close_price + 1.25 * atr
                    target = close_price - 2.5 * atr

                log_message(f"{direction.upper()} Signal -> {symbol} @ {close_price:.2f} | SL: {sl:.2f}, Target: {target:.2f}")
                send_telegram(f"{direction.upper()} Signal -> {symbol} @ {close_price:.2f} | SL: {sl:.2f}, Target: {target:.2f}")

                entry_resp = dhan_place_order(symbol, direction, quantity)
                if entry_resp["status"] != "success":
                    log_message(f"Entry order failed: {entry_resp}")
                    continue

                sl_resp = dhan_place_sl_order(symbol, direction, close_price)
                tgt_resp = dhan_place_target_order(symbol, direction, close_price)

                if sl_resp["status"] == "success" and tgt_resp["status"] == "success":
                    log_message(f"Trade confirmed for {symbol}. Entry, SL, and Target placed.")
                    last_trade_time[symbol] = datetime.now()
                    thread = threading.Thread(
                        target=monitor_position,
                        args=(symbol, direction, close_price, sl, target),
                        daemon=True
                    )
                    thread.start()
                else:
                    log_message(f"SL or Target failed: SL={sl_resp}, TGT={tgt_resp}")

            time.sleep(120)
        except Exception as e:
            log_message(f"Unexpected error: {str(e)}")
            send_telegram(f"Strategy Error: {str(e)}")
            time.sleep(30)

# =====================
# Graceful Exit
# =====================
def graceful_exit(sig, frame):
    log_message("WMA_Dhan_Graceful shutdown requested.")
    send_telegram("WMA_Dhan_Strategy stopped gracefully.")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

# =====================
# Start Script
# =====================
if __name__ == "__main__":
    try:
        if mode.lower() == "live":
            run_strategy()
        else:
            log_message(f"Invalid mode: {mode}")
    except Exception as e:
        log_message(f"Fatal Error: {e}")
        send_telegram(f"🔥 Fatal Error: {e}")
        time.sleep(60)
