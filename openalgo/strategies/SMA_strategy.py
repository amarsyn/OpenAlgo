# =====================================
# SMART MA Trend Strategy
# (Synchronized Momentum And Risk-managed Trends)
# =====================================
# Description:
# Intraday trend-following strategy combining HMA/WMA trend detection, MACD confirmation,
# percentile-based RSI filters, ATR-based SL/target, VWAP context, and volume validation.
# -------------------------------------
# Requirements: openalgo API, pandas, pandas_ta
# =====================================

# ===== Import Dependencies =====
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta, date
import time
import os
import sys
import signal
import requests
from openalgo import api

# ===== Configuration Parameters =====
api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy_name = "SMART MA Trend Strategy"
symbols = ["SBIN", "ADANIPORTS", "ICICIBANK"]  # Customize your symbols here
exchange = "NSE"
product = "MIS"
quantity = 5
mode = "live"

# Trading window and trade management
start_time = "09:19"
end_time = "14:30"
max_trades = 3
cooldown_minutes = 15

# Logging setup
LOG_FILE = f"logs/SMA_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/SMA_trades_{datetime.now().strftime('%Y-%m-%d')}.csv"
os.makedirs("logs", exist_ok=True)

# API client setup
client = api(api_key=api_key)
trade_count = 0
last_trade_time = datetime.min

# Telegram alerts setup
TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

# ===== Utility Functions =====
def send_telegram(msg):
    if TELEGRAM_ENABLED:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": msg})

def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"[{timestamp}] SMART_MA {msg}")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")

def log_trade(symbol, entry, exit, profit_pct, reason):
    with open(TRADE_LOG, "a") as f:
        f.write(f"{datetime.now()},{symbol},{entry},{exit},{profit_pct:.2f},{reason}\n")

# ===== Fetch Market Data =====
def fetch_data(symbol):
    end = datetime.now()
    start = end - timedelta(days=5)
    result = client.history(symbol=symbol, exchange=exchange, interval="5m", start_date=start.strftime("%Y-%m-%d"), end_date=end.strftime("%Y-%m-%d"))

    # Enhanced debugging
    if isinstance(result, dict):
        if "data" not in result or not result["data"]:
            log(f"[DEBUG] API returned dict without 'data' for {symbol}: Keys = {list(result.keys())}")
            return None
        df = pd.DataFrame(result["data"])
    elif isinstance(result, pd.DataFrame):
        df = result
    else:
        log(f"[DEBUG] Unexpected type from API for {symbol}: {type(result)}")
        return None

    if df.empty:
        log(f"[DEBUG] DataFrame empty for {symbol}")
        return None

    df.index = pd.to_datetime(df.index)
    df['wma'] = ta.wma(df['close'], length=20)
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['macd'] = ta.macd(df['close'])['MACD_12_26_9']
    df['macd_signal'] = ta.macd(df['close'])['MACDs_12_26_9']
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    df['vwap'] = (df['volume'] * (df['high'] + df['low'] + df['close']) / 3).cumsum() / df['volume'].cumsum()
    df['rsi_upper'] = df['rsi'].rolling(50).quantile(0.75)
    df['rsi_lower'] = df['rsi'].rolling(50).quantile(0.25)
    df['vol_ma'] = df['volume'].rolling(20).mean()
    return df

# ===== Trading Window Validation =====
def valid_trade_time():
    now = datetime.now().strftime("%H:%M")
    return start_time <= now <= end_time

# ===== Entry Conditions =====
def is_bullish(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    return (latest['close'] > latest['wma'] and latest['wma'] > prev['wma'] and
            latest['close'] > latest['vwap'] and latest['rsi'] > latest['rsi_upper'] and
            latest['macd'] > latest['macd_signal'] and latest['atr'] >= 1 and
            latest['volume'] > 0.2 * latest['vol_ma'])

def is_bearish(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    return (latest['close'] < latest['wma'] and latest['wma'] < prev['wma'] and
            latest['close'] < latest['vwap'] and latest['rsi'] < latest['rsi_lower'] and
            latest['macd'] < latest['macd_signal'] and latest['atr'] >= 1 and
            latest['volume'] > 0.2 * latest['vol_ma'])

# ===== Order Execution =====
def place_order(symbol, direction):
    action = "BUY" if direction == "bullish" else "SELL"
    try:
        response = client.placeorder(strategy=strategy_name, symbol=symbol, action=action, exchange=exchange,
                                     price_type="MARKET", product=product, quantity=quantity)
        ltp = client.quotes(symbol=symbol, exchange=exchange)['data']['ltp']
        return response.get("orderid"), ltp
    except Exception as e:
        log(f"Order failed for {symbol}: {e}")
        return None, None

# ===== Trade Management Loop =====
def monitor_trade(symbol, direction, entry_price, atr):
    sl = entry_price - atr * 1.2 if direction == "bullish" else entry_price + atr * 1.2
    target = entry_price + atr * 2.5 if direction == "bullish" else entry_price - atr * 2.5
    trailing_trigger = entry_price * 1.01 if direction == "bullish" else entry_price * 0.99
    tsl = sl

    while True:
        time.sleep(30)
        ltp = client.quotes(symbol=symbol, exchange=exchange)['data']['ltp']

        if direction == "bullish":
            if ltp >= target:
                log(f"Target hit {symbol} @ {ltp:.2f}"); send_telegram(f"Target hit: {symbol} @ {ltp:.2f}")
                log_trade(symbol, entry_price, ltp, (ltp-entry_price)/entry_price*100, "Target")
                break
            elif ltp <= tsl:
                log(f"SL hit {symbol} @ {ltp:.2f}"); send_telegram(f"SL hit: {symbol} @ {ltp:.2f}")
                log_trade(symbol, entry_price, ltp, (ltp-entry_price)/entry_price*100, "SL")
                break
            elif ltp >= trailing_trigger:
                tsl = max(tsl, ltp - 0.0075 * ltp)
        else:
            if ltp <= target:
                log(f"Target hit {symbol} @ {ltp:.2f}"); send_telegram(f"Target hit: {symbol} @ {ltp:.2f}")
                log_trade(symbol, entry_price, ltp, (entry_price-ltp)/entry_price*100, "Target")
                break
            elif ltp >= tsl:
                log(f"SL hit {symbol} @ {ltp:.2f}"); send_telegram(f"SL hit: {symbol} @ {ltp:.2f}")
                log_trade(symbol, entry_price, ltp, (entry_price-ltp)/entry_price*100, "SL")
                break
            elif ltp <= trailing_trigger:
                tsl = min(tsl, ltp + 0.0075 * ltp)

# ===== Strategy Core Loop =====
def run_strategy():
    global trade_count, last_trade_time
    for symbol in symbols:
        if trade_count >= max_trades or (datetime.now() - last_trade_time).seconds < cooldown_minutes * 60:
            continue

        df = fetch_data(symbol)
        if df is None or len(df) < 30:
            continue

        direction = None
        if is_bullish(df):
            direction = "bullish"
        elif is_bearish(df):
            direction = "bearish"

        if direction:
            entry_price = df.iloc[-1]['close']
            atr = df.iloc[-1]['atr']
            log(f"{direction.upper()} ENTRY: {symbol} @ {entry_price:.2f} | ATR: {atr:.2f}")
            send_telegram(f"{direction.upper()} ENTRY {symbol} @ {entry_price:.2f}")
            order_id, _ = place_order(symbol, direction)
            if order_id:
                trade_count += 1
                last_trade_time = datetime.now()
                monitor_trade(symbol, direction, entry_price, atr)

# ===== Graceful Shutdown =====
def graceful_exit(signum, frame):
    log("Graceful shutdown invoked.")
    send_telegram("SMART_MA Strategy stopped gracefully.")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

# ===== Main Execution Block =====
if __name__ == '__main__':
    log("SMART MA Strategy started.")
    send_telegram("SMART MA Strategy LIVE.")
    while True:
        if valid_trade_time():
            run_strategy()
        time.sleep(60)
