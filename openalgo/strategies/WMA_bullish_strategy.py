# =======================
# Import Dependencies
# =======================
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta, date
import time
import sys
import signal
from openalgo import api
import requests
import os

# ================================
# 📁 Setup and Configuration
# ================================
# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# 🔧 Test if logging works (check file permission/path issues)
with open("test_log.txt", "a") as f:
    f.write("Log test\n")
    
# =======================
# Configuration Settings
# =======================
api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy = "Weighted MA Bullish Trend Python"
symbols = ["INDUSTOWER"]
exchange = "NSE"
product = "MIS"
quantity = 10
mode = "live"  # or "live"

# Entry Time Filter (24-hr format)
start_time = "09:20"
end_time = "14:30"

# Stop Loss and Target (in %)
stop_loss_pct = 0.3
target_pct = 1.2
trailing_sl_pct = 0.3
trailing_trigger_pct = 0.35

# Logging
LOG_FILE = f"logs/WMA_bullish_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/WMA_bullish_{datetime.now().strftime('%Y-%m-%d')}.csv"

TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

client = api(api_key=api_key)
trade_count = 0
max_trades_per_day = 2
today = date.today()

# =======================
# Utility Functions
# =======================
def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        requests.post(url, data=payload)

def log_message(message):
    timestamped = f"{datetime.now()} - {message}"
    print(timestamped)
    with open(LOG_FILE, "a") as log_file:
        log_file.write(f"{timestamped}\n")

def log_trade_csv(symbol, entry_price, close_price, profit_pct, reason):
    with open(TRADE_LOG_CSV, "a") as log_file:
        log_file.write(f"{datetime.now()},{symbol},{entry_price},{close_price},{(close_price - entry_price)/entry_price * 100:.2f},{profit_pct:.2f},{reason},Trailing SL\n")

# =======================
# Market Data Fetching
# =======================
def fetch_data(symbol):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=20)
    df = client.history(
        symbol=symbol,
        exchange=exchange,
        interval="5m",
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d")
    )
    df.index = pd.to_datetime(df.index)
    df['wma'] = ta.wma(df['close'], length=20)
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    df['macd'] = ta.macd(df['close']).iloc[:, 0]
    df['macd_signal'] = ta.macd(df['close']).iloc[:, 1]
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    return df

# =======================
# Entry Condition Check (lines 123–138)
# =======================
def check_entry_conditions(df):
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    log_message(f"Checking condition: close={latest['close']}, wma={latest['wma']}, prev_wma={previous['wma']}, rsi={latest['rsi']}, vol={latest['volume']}, vol_ma={latest['vol_ma']}, macd={latest['macd']}, macd_signal={latest['macd_signal']}, atr={df['atr'].iloc[-1]}")

    if df['atr'].iloc[-1] < 1.0:
        log_message("ATR too low, skipping entry.")
        return False

    if latest['volume'] <= 0.5 * latest['vol_ma']:
        log_message("Volume too low compared to average.")
        return False

    if not (latest['macd'] > latest['macd_signal'] or (latest['macd'] > 0 and latest['macd_signal'] < 0)):
        log_message("MACD conditions not met.")
        return False

    if (
        latest['close'] > latest['wma'] and
        latest['wma'] > previous['wma'] and
        latest['rsi'] > 60
    ):
        log_message("Bullish entry condition met.")
        return True

    log_message("Price/RSI/WMA trend conditions not met.")
    return False

    if latest['volume'] <= 0.5 * latest['vol_ma']:
        log_message("Volume too low compared to average.")
        return False

    if not (latest['macd'] < latest['macd_signal'] or (latest['macd'] < 0 and latest['macd_signal'] > 0)):
        log_message("MACD conditions not met.")
        return False

    if (
        latest['close'] < latest['wma'] and
        latest['wma'] < previous['wma'] and
        latest['rsi'] < 40
    ):
        log_message("Bullish entry condition met.")
        return True

    log_message("Price/RSI/WMA trend conditions not met.")
    return False

# =======================
# Order Placement and Strategy Execution
# =======================
# Order Placement (Short Sell)
def place_order(symbol):
    try:
        response = client.placeorder(
            strategy=strategy,
            symbol=symbol,
            action="SELL",  # reversed
            exchange=exchange,
            price_type="MARKET",
            product=product,
            quantity=quantity
        )
        return response['orderid'], client.quotes(symbol=symbol, exchange=exchange)['data']['ltp']
    except Exception as e:
        send_telegram(f"Order failed for {symbol}: {str(e)}")
        log_message(f"Order failed for {symbol}: {str(e)}")
        return None, None

# Exit Order (Buy back)
def exit_position(symbol):
    try:
        response = client.placeorder(
            strategy=strategy,
            symbol=symbol,
            action="BUY",  # reversed
            exchange=exchange,
            price_type="MARKET",
            product=product,
            quantity=quantity
        )
        send_telegram(f"✅ Exit Order Placed for {symbol}, Order ID: {response['orderid']}")
        log_message(f"Exit Order Placed for {symbol}, Order ID: {response['orderid']}")
    except Exception as e:
        send_telegram(f"Exit Order failed for {symbol}: {str(e)}")
        log_message(f"Exit Order failed for {symbol}: {str(e)}")    

def graceful_exit(signum, frame):
    print("Graceful shutdown requested... Exiting strategy.")
    send_telegram("🛑 Strategy stopped gracefully.")
    log_message("Graceful shutdown invoked.")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

def run_strategy():
    global trade_count, today
    now = datetime.now()
    if date.today() != today:
        trade_count = 0
        today = date.today()

    if not (start_time <= now.strftime("%H:%M") <= end_time) or trade_count >= max_trades_per_day:
        log_message("Outside trading window or max trades reached.")
        return

    for symbol in symbols:
        if trade_count >= max_trades_per_day:
            break

        log_message(f"Processing {symbol}...")
        df = fetch_data(symbol)
        if check_entry_conditions(df):
            order_id, entry_price = place_order(symbol)
            if order_id and entry_price:
                trade_count += 1
                msg = f"Order Placed for {symbol}, Order ID: {order_id} at {entry_price}"
                send_telegram(msg)
                log_message(msg)

                atr_sl = df['atr'].iloc[-1]
                max_sl = entry_price * (1 + 0.6 / 100)
                sl_price = max(entry_price - atr_sl, entry_price * (1 - 0.6 / 100))
                log_message(f"SL for {symbol} set at {sl_price:.2f} (ATR: {atr_sl:.2f})")
                target_price = entry_price * (1 + target_pct / 100)
                trailing_trigger = entry_price * (1 + trailing_trigger_pct / 100)

                while True:
                    time.sleep(60)
                    ltp = client.quotes(symbol=symbol, exchange=exchange)['data']['ltp']

                    if ltp <= sl_price:
                        send_telegram(f"🔻 Stop Loss hit for {symbol} at {ltp}")
                        log_message(f"Stop Loss hit for {symbol} at {ltp}")
                        log_trade_csv(symbol, entry_price, ltp, ((ltp-entry_price)/entry_price)*100, "Stop Loss")
                        exit_position(symbol)
                        pl_pct = ((ltp - entry_price) / entry_price) * 100
                        log_message(f"Trade closed for {symbol}, P/L: {pl_pct:.2f}%")
                        break
                    elif ltp >= target_price:
                        send_telegram(f"🎯 Target hit for {symbol} at {ltp}")
                        log_message(f"Target hit for {symbol} at {ltp}")
                        log_trade_csv(symbol, entry_price, ltp, ((ltp-entry_price)/entry_price)*100, "Target Hit")
                        exit_position(symbol)
                        pl_pct = ((ltp - entry_price) / entry_price) * 100
                        log_message(f"Trade closed for {symbol}, P/L: {pl_pct:.2f}%")
                        break
                    elif ltp >= trailing_trigger:
                        new_sl = ltp * (1 + trailing_sl_pct / 100)
                        if new_sl > sl_price:
                            sl_price = new_sl
                            send_telegram(f"🔁 Trailing SL updated for {symbol} to {sl_price:.2f}")
                            log_message(f"Trailing SL updated for {symbol} to {sl_price:.2f}")

# =======================
# Main Execution
# =======================
if __name__ == '__main__':
    print("Starting Amar's WMA Bullish Multi-Stock Strategy...")
    send_telegram(f"✅ Amar's WMA Bullish Multi-Stock strategy started in {mode.upper()} mode.")
    log_message(f"Amar's WMA Bullish Strategy started in {mode.upper()} mode.")

    while True:
        run_strategy()
        time.sleep(30)  # Run every 5 minutes
