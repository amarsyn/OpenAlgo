# =======================
# Import Dependencies
# =======================
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
import time
import sys
import signal
from openalgo import api
import requests
import os

# ==============================
# Setup and Configuration
# ==============================
os.makedirs("logs", exist_ok=True)

api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy_name = "WMA Dynamic Strategy"
symbols = ["TCS", "INFY", "HCLTECH", "MUTHOOTFIN"]
exchange = "NSE"
product = "MIS"
quantity = 10
mode = "live"
start_time = "09:20"
end_time = "15:30"
sl_pct = 0.3
target_pct = 1.2
trailing_sl_pct = 0.3
trailing_trigger_pct = 0.5

LOG_FILE = f"logs/WMA_dynamic_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/WMA_dynamic_{datetime.now().strftime('%Y-%m-%d')}.csv"

TELEGRAM_ENABLED = True
BOT_TOKEN = "<TELEGRAM_BOT_TOKEN>"
CHAT_ID = "<TELEGRAM_CHAT_ID>"

client = api(api_key=api_key)

# =====================
# Utility Functions
# =====================
def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        requests.post(url, data=payload)

def log_message(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"[{timestamp}] WMA_Dynamic {msg}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] WMA_Dynamic {msg}\n")

def log_trade(symbol, entry_price, exit_price, profit_pct, reason):
    with open(TRADE_LOG, "a") as f:
        f.write(f"{datetime.now()},{symbol},{entry_price},{exit_price},{profit_pct:.2f},{reason}\n")

# =======================
# MACD Crossover Utility
# =======================
def recent_macd_bullish_cross(df, lookback=5):
    macd = df['macd']
    macd_signal = df['macd_signal']
    for i in range(-lookback - 1, -1):
        if macd.iloc[i - 1] < macd_signal.iloc[i - 1] and macd.iloc[i] > macd_signal.iloc[i]:
            return True
    return False

def recent_macd_bearish_cross(df, lookback=5):
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
    start_date = end_date - timedelta(days=20)
    result = client.history(
        symbol=symbol,
        exchange=exchange,
        interval="5m",
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d")
    )

    if isinstance(result, dict) and "data" in result:
        df = pd.DataFrame(result["data"])
    elif isinstance(result, pd.DataFrame):
        df = result
    else:
        log_message(f"No valid data returned for {symbol}")
        return None

    if df.empty:
        log_message(f"Empty DataFrame for {symbol}")
        return None

    df.index = pd.to_datetime(df.index)
    df['wma'] = ta.wma(df['close'], length=20)
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['vol_ma'] = df['volume'].rolling(20).mean()
    macd_df = ta.macd(df['close'])
    df['macd'] = macd_df['MACD_12_26_9']
    df['macd_signal'] = macd_df['MACDs_12_26_9']
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    return df

# =====================
# Trend Detection
# =====================
def detect_trend(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    log_message(f"Checking trend — Close: {last['close']}, WMA: {last['wma']}, Prev_WMA: {prev['wma']}, RSI: {last['rsi']}, MACD: {last['macd']}, Signal: {last['macd_signal']}")

    if (
        last['close'] > last['wma'] and
        last['wma'] > prev['wma'] and
        last['rsi'] > 60 and
        recent_macd_bullish_cross(df, lookback=3)
    ):
        return "bullish"

    if (
        last['close'] < last['wma'] and
        last['wma'] < prev['wma'] and
        last['rsi'] < 40 and
        recent_macd_bearish_cross(df, lookback=3)
    ):
        return "bearish"

    return None

# =======================
# Trailing SL Logic
# =======================
def monitor_position(symbol, direction, entry_price, sl, target):
    current_sl = sl
    while True:
        time.sleep(30)
        try:
            ltp = client.quotes(symbol=symbol, exchange=exchange)['data']['ltp']
        except:
            continue

        if direction == "bullish":
            if ltp >= target:
                log_message(f"Target hit for {symbol} at {ltp:.2f}")
                send_telegram(f"Target hit: {symbol} @ {ltp:.2f}")
                log_trade(symbol, entry_price, ltp, target_pct, "Target")
                exit_position(symbol, direction)
                break
            elif ltp <= current_sl:
                log_message(f"SL hit for {symbol} at {ltp:.2f}")
                send_telegram(f"SL hit: {symbol} @ {ltp:.2f}")
                log_trade(symbol, entry_price, ltp, -sl_pct, "SL")
                exit_position(symbol, direction)
                break
            elif ltp >= entry_price * (1 + trailing_trigger_pct / 100):
                new_sl = ltp * (1 - trailing_sl_pct / 100)
                if new_sl > current_sl:
                    current_sl = new_sl
                    log_message(f"TSL updated for {symbol} to {current_sl:.2f}")

        elif direction == "bearish":
            if ltp <= target:
                log_message(f"Target hit for {symbol} at {ltp:.2f}")
                send_telegram(f"Target hit: {symbol} @ {ltp:.2f}")
                log_trade(symbol, entry_price, ltp, target_pct, "Target")
                exit_position(symbol, direction)
                break
            elif ltp >= current_sl:
                log_message(f"SL hit for {symbol} at {ltp:.2f}")
                send_telegram(f"SL hit: {symbol} @ {ltp:.2f}")
                log_trade(symbol, entry_price, ltp, -sl_pct, "SL")
                exit_position(symbol, direction)
                break
            elif ltp <= entry_price * (1 - trailing_trigger_pct / 100):
                new_sl = ltp * (1 + trailing_sl_pct / 100)
                if new_sl < current_sl:
                    current_sl = new_sl
                    log_message(f"TSL updated for {symbol} to {current_sl:.2f}")

# =====================
# Order Placement
# =====================
def place_order(symbol, direction):
    action = "BUY" if direction == "bullish" else "SELL"
    try:
        response = client.placeorder(
            strategy=strategy_name,
            symbol=symbol,
            action=action,
            exchange=exchange,
            price_type="MARKET",
            product=product,
            quantity=quantity
        )
        ltp = client.quotes(symbol=symbol, exchange=exchange)['data']['ltp']
        return response['orderid'], ltp
    except Exception as e:
        log_message(f"Order failed for {symbol}: {str(e)}")
        send_telegram(f"❌ Order failed for {symbol}: {str(e)}")
        return None, None

def exit_position(symbol, direction):
    action = "SELL" if direction == "bullish" else "BUY"
    try:
        response = client.placeorder(
            strategy=strategy_name,
            symbol=symbol,
            action=action,
            exchange=exchange,
            price_type="MARKET",
            product=product,
            quantity=quantity
        )
        log_message(f"Exit Order Placed for {symbol}. Order ID: {response['orderid']}")
        send_telegram(f"✅ Exit Order for {symbol}, ID: {response['orderid']}")
    except Exception as e:
        log_message(f"Exit failed for {symbol}: {str(e)}")
        send_telegram(f"❌ Exit failed for {symbol}: {str(e)}")

# =====================
# Main Strategy Loop
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
                df = fetch_data(symbol)
                if df is None or len(df) < 30:
                    continue

                direction = detect_trend(df)
                if not direction:
                    log_message("No valid trend detected.")
                    continue

                entry_price = df.iloc[-1]['close']
                atr = df.iloc[-1]['atr']
                sl = entry_price - atr if direction == "bullish" else entry_price + atr
                target = entry_price + 2 * atr if direction == "bullish" else entry_price - 2 * atr

                log_message(f"{direction.upper()} Signal -> {symbol} @ {entry_price:.2f} | SL: {sl:.2f}, Target: {target:.2f}")
                send_telegram(f"{direction.upper()} ENTRY for {symbol}: {entry_price:.2f}")

                order_id, ltp = place_order(symbol, direction)
                if order_id:
                    monitor_position(symbol, direction, entry_price, sl, target)

            time.sleep(300)
        except Exception as e:
            log_message(f"Unexpected error: {str(e)}")
            send_telegram(f"Strategy Error: {str(e)}")
            time.sleep(60)

# =====================
# Graceful Exit
# =====================
def graceful_exit(sig, frame):
    log_message("Graceful shutdown requested.")
    send_telegram("Strategy stopped gracefully.")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

if __name__ == "__main__":
    run_strategy()
