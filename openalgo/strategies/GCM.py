from openalgo import api
import pandas as pd
import numpy as np
import time
import requests
import os
from datetime import datetime, timedelta
import signal
import sys
import pandas_ta as ta

# ================================
# 📁 Setup and Configuration
# ================================
# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# 🔧 Test if logging works (check file permission/path issues)
with open("test_log.txt", "a") as f:
    f.write("Log test\n")

# Configuration and Threshold Parameters
api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy = "Green Cloud Momentum"
symbols = ["ICICIBANK", "HDFCBANK","ADANIENT"]
exchange = "NSE"
product = "MIS"
quantity = 5
mode = "live"  # Set to "analyze" for Analyzer Mode - Toggle between live and analyze mode

stop_loss_pct = 2
target_pct = 4
trailing_sl_pct = 0.75
trailing_trigger_pct = 1

TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

start_time = "09:20"
end_time = "14:45"
start_date = "2024-03-01"
end_date = "2025-12-01"

LOG_FILE = f"logs/GCM_B1_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/GCM_B1_{datetime.now().strftime('%Y-%m-%d')}.csv"

positions = {sym: 0 for sym in symbols}
entry_prices = {sym: None for sym in symbols}
max_favorable_price = {sym: None for sym in symbols}
entry_times = {sym: None for sym in symbols}
trade_counts = {sym: 0 for sym in symbols}
partial_booked = {sym: False for sym in symbols}

client = api(api_key=api_key, host='http://127.0.0.1:5000')

# ----------------------------------------------------
# Function: send_telegram
# Sends real-time alerts to Telegram based on bot token and chat ID
# ----------------------------------------------------
def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        response = requests.post(url, data=payload)
        # print(f"📩 Telegram response: {response.text}")

# ----------------------------------------------------
# Function: log_message
# Logs messages to both console and a timestamped log file
# ----------------------------------------------------
def log_message(msg):
    short_msg = f"{datetime.now().strftime('%H:%M:%S')} - {msg}"
    print(short_msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} - {msg}\n")
    
# ----------------------------------------------------
# Function: add_indicators
# Adds RSI, VWAP, and ATR to the OHLCV dataframe for trade signal logic
# ----------------------------------------------------
def add_indicators(df):
    df['RSI'] = ta.rsi(df['close'], length=14)
    df['VWAP'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
    df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    return df
# ----------------------------------------------------
# Function: within_trading_hours
# Checks if the current time is within the defined intraday trading window
# ----------------------------------------------------
def within_trading_hours():
    now = datetime.now().time()
    return datetime.strptime(start_time, "%H:%M").time() <= now <= datetime.strptime(end_time, "%H:%M").time()

# ----------------------------------------------------
# Function: place_exit
# Executes a market order to exit an open position, resets internal tracking state
# ----------------------------------------------------
def place_exit(symbol, side):
    action = "SELL" if side == "long" else "BUY"
    response = client.placeorder(strategy=strategy, symbol=symbol, action=action,
                                 exchange=exchange, price_type="MARKET",
                                 product=product, quantity=abs(positions[symbol]))
    log_message(f"\U0001F501 EXIT {side.upper()} {symbol} at market: {response}")
    send_telegram(f"EXIT {side.upper()} {symbol}")
    positions[symbol] = 0
    entry_prices[symbol] = None
    max_favorable_price[symbol] = None
    entry_times[symbol] = None
    partial_booked[symbol] = False

# ----------------------------------------------------
# Function: Green Cloud Momentum
# Core logic that runs the strategy loop: signal evaluation, trade execution,
# trailing SL handling, partial booking, and exit logic
# ----------------------------------------------------
def green_cloud_momentum(symbol, df):
    try:
        df = add_indicators(df)
        df['EMA_9'] = ta.ema(df['close'], length=9)
        df['EMA_21'] = ta.ema(df['close'], length=21)

        ichimoku = ta.ichimoku(df['high'], df['low'], df['close'])
        df['tenkan_sen'] = ichimoku['ITS_9']
        df['kijun_sen'] = ichimoku['KS_26']
        df['senkou_span_a'] = ichimoku['SSA_9_26']
        df['senkou_span_b'] = ichimoku['SSB_9_26']
        df['chikou_span'] = df['close'].shift(-26)

        latest = df.iloc[-1]

        long_cond = (
            latest['EMA_9'] > latest['EMA_21'] and
            latest['close'] > latest['VWAP'] and
            latest['EMA_9'] > latest['VWAP'] and
            latest['close'] > latest['senkou_span_a'] > latest['senkou_span_b'] and
            latest['tenkan_sen'] > latest['kijun_sen'] and
            latest['chikou_span'] > latest['close']
        )

        short_cond = (
            latest['EMA_9'] < latest['EMA_21'] and
            latest['close'] < latest['VWAP'] and
            latest['EMA_9'] < latest['VWAP'] and
            latest['close'] < latest['senkou_span_a'] < latest['senkou_span_b'] and
            latest['tenkan_sen'] < latest['kijun_sen'] and
            latest['chikou_span'] < latest['close']
        )

        current_pos = positions[symbol]

        if current_pos > 0 and short_cond:
            place_exit(symbol, "long")
            trade_counts[symbol] += 1

        if current_pos < 0 and long_cond:
            place_exit(symbol, "short")
            trade_counts[symbol] += 1

        if current_pos == 0:
            if long_cond:
                response = client.placeorder(strategy=strategy, symbol=symbol, action="BUY",
                                             exchange=exchange, price_type="MARKET",
                                             product=product, quantity=quantity)
                entry_price = latest['close']
                positions[symbol] = quantity
                entry_prices[symbol] = entry_price
                max_favorable_price[symbol] = entry_price
                entry_times[symbol] = datetime.now()
                log_message(f"🟢 LONG ENTRY: {symbol} at {entry_price}")
                send_telegram(f"🟢 LONG ENTRY: {symbol} at {entry_price}")

            elif short_cond:
                response = client.placeorder(strategy=strategy, symbol=symbol, action="SELL",
                                             exchange=exchange, price_type="MARKET",
                                             product=product, quantity=quantity)
                entry_price = latest['close']
                positions[symbol] = -quantity
                entry_prices[symbol] = entry_price
                max_favorable_price[symbol] = entry_price
                entry_times[symbol] = datetime.now()
                log_message(f"🔴 SHORT ENTRY: {symbol} at {entry_price}")
                send_telegram(f"🔴 SHORT ENTRY: {symbol} at {entry_price}")

        if current_pos != 0:
            direction = 1 if current_pos > 0 else -1
            current_price = latest['close']
            entry_price = entry_prices[symbol]
            mfp = max_favorable_price[symbol]

            if direction * current_price > direction * mfp:
                max_favorable_price[symbol] = current_price

            move_trigger = abs(entry_price) * trailing_trigger_pct / 100
            if abs(current_price - entry_price) >= move_trigger:
                tsl_price = max_favorable_price[symbol] - direction * (entry_price * trailing_sl_pct / 100)
                if direction * current_price <= direction * tsl_price:
                    log_message(f"🔁 TRAILING SL HIT for {symbol} at {current_price}")
                    place_exit(symbol, "long" if current_pos > 0 else "short")
                    trade_counts[symbol] += 1

    except Exception as e:
        log_message(f"❌ Error in strategy logic for {symbol}: {e}")

# ----------------------------------------------------
# Main block: starts the strategy, handles shutdown gracefully
# ----------------------------------------------------
if __name__ == "__main__":
    print("Starting Amar's GCM Batch-1 Multi-Stock Strategy...")
    send_telegram(f"✅ Amar's GCM Batch-1 Multi-Stock strategy started in {mode.upper()} mode.")
    log_message(f"Amar's GCM Batch-1 Strategy started in {mode.upper()} mode.")

    def graceful_exit(signum, frame):
        print("Graceful shutdown requested... Exiting strategy.")
        send_telegram("🛑 GCM Batch-1 Strategy stopped gracefully.")
        log_message("Graceful shutdown invoked.")
        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)

    while True:
        if within_trading_hours():
            for symbol in symbols:
                try:
                    df = client.get_ohlcv(symbol, interval='5m', lookback=100)
                    if df is not None and not df.empty:
                        green_cloud_momentum(symbol, df)
                except Exception as e:
                    print(f"Error in strategy: {str(e)}")
                    log_message(f"❌ Error: {symbol} -> {e}")
        time.sleep(30)