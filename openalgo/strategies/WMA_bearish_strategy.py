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
strategy = "Weighted MA Bearish Trend Python"
symbols = ["SUNPHARMA","TCS","HEROMOTOCO","ITC","SBIN"]
exchange = "NSE"
product = "MIS"
quantity = 10
mode = "live"

# Entry Time Filter (24-hr format)
start_time = "09:20"
end_time = "14:30"

# Stop Loss and Target (in %)
stop_loss_pct = 1.0
target_pct = 2.0
trailing_sl_pct = 0.5
trailing_trigger_pct = 0.65

# Logging
LOG_FILE = f"logs/WMA_bearish_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/WMA_bearish_{datetime.now().strftime('%Y-%m-%d')}.csv"

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

def log_message(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"[{timestamp}] WMA_Bearish {msg}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] WMA_Bearish {msg}\n")

def log_trade_csv(symbol, entry_price, close_price, profit_pct, reason):
    with open(TRADE_LOG, "a") as log_file:
        log_file.write(f"{datetime.now()},{symbol},{entry_price},{close_price},{(close_price - entry_price)/entry_price * 100:.2f},{profit_pct:.2f},{reason},Trailing SL\n")

# =======================
# Market Data Fetching
# =======================
def fetch_data(symbol):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=20)
    data = client.history(
        symbol=symbol,
        exchange=exchange,
        interval="5m",
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d")
    )

    if isinstance(data, dict) and "data" in data:
        df = pd.DataFrame(data["data"])
    elif isinstance(data, pd.DataFrame):
        df = data
    else:
        log_message(f"Unexpected data format received for {symbol}")
        return None

    if df.empty:
        log_message(f"No historical data found for {symbol}")
        return None

    df.index = pd.to_datetime(df.index)
    df['wma'] = ta.wma(df['close'], length=20)
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    macd_df = ta.macd(df['close'])
    df['macd'] = macd_df['MACD_12_26_9']
    df['macd_signal'] = macd_df['MACDs_12_26_9']
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    return df

# =======================
# MACD Crossover Utility
# =======================
def recent_macd_bearish_cross(df, lookback=5):
    macd = df['macd']
    macd_signal = df['macd_signal']
    for i in range(-lookback - 1, -1):
        if macd.iloc[i - 1] > macd_signal.iloc[i - 1] and macd.iloc[i] < macd_signal.iloc[i]:
            return True
    return False

# =======================
# Entry Condition Check
# =======================
def check_entry_conditions(df):
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    log_message(f"Checking condition: close={latest['close']}, wma={latest['wma']}, prev_wma={previous['wma']}, rsi={latest['rsi']}, vol={latest['volume']}, vol_ma={latest['vol_ma']}, macd={latest['macd']}, macd_signal={latest['macd_signal']}, atr={df['atr'].iloc[-1]}")

    if df['atr'].iloc[-1] < 0.5:
        log_message("ATR too low, skipping entry.")
        return False

    if latest['volume'] <= 0.5 * latest['vol_ma']:
        log_message("Volume too low compared to average.")
        return False

    if not recent_macd_bearish_cross(df, lookback=3):
        log_message("No recent MACD bearish crossover.")
        return False

    if (
        latest['close'] < latest['wma'] and
        latest['wma'] < previous['wma'] and
        latest['rsi'] < 40
    ):
        log_message("Bearish entry condition met.")
        return True

    log_message("Price/RSI/WMA trend conditions not met.")
    return False

# =======================
# Order Placement and Strategy Execution
# =======================
def place_order(symbol):
    try:
        response = client.placeorder(
            strategy=strategy,
            symbol=symbol,
            action="SELL",
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

def exit_position(symbol):
    try:
        response = client.placeorder(
            strategy=strategy,
            symbol=symbol,
            action="BUY",
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

# =======================
# Strategy Execution
# =======================
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
        if df is None:
            log_message(f"No data received for {symbol}, skipping.")
            continue

        if check_entry_conditions(df):
            order_id, entry_price = place_order(symbol)
            if order_id and entry_price:
                trade_count += 1
                send_telegram(f"Order Placed for {symbol}, Order ID: {order_id} at {entry_price}")
                log_message(f"Order Placed for {symbol}, Order ID: {order_id} at {entry_price}")

                atr_sl = df['atr'].iloc[-1]
                max_sl = entry_price * (1 + 0.6 / 100)
                sl_price = entry_price + 1.2 * atr_sl
                if abs(entry_price - sl_price) / entry_price < 0.003:
                    log_message("SL buffer too narrow, skipping trade.")
                    exit_position(symbol)
                    return

                target_price = entry_price * (1 - target_pct / 100)
                partial_target_price = entry_price * (1 - 0.008)
                trailing_trigger = entry_price * (1 - trailing_trigger_pct / 100)

                log_message(f"SL for {symbol} set at {sl_price:.2f} (ATR: {atr_sl:.2f})")

                partial_booked = False
                trend_reversed = False

                while True:
                    time.sleep(60)
                    try:
                        df = fetch_data(symbol)
                        if df is None:
                            log_message(f"Data fetch failed for {symbol} in monitor loop.")
                            break

                        quote = client.quotes(symbol=symbol, exchange=exchange)
                        ltp = quote['data']['ltp']
                        log_message(f"LTP for {symbol}: {ltp:.2f} | SL: {sl_price:.2f} | Target: {target_price:.2f}")
                        # --- Dynamic Target Update ---
                        latest_atr = df['atr'].iloc[-1]
                        dynamic_target = ltp - 1.5 * latest_atr  # You can adjust multiplier (e.g., 1.2 or 2.0)

                        if dynamic_target < target_price:
                            target_price = dynamic_target
                            log_message(f"📉 Dynamic target updated to {target_price:.2f} using ATR={latest_atr:.2f}")

                    except Exception as e:
                        log_message(f"Quote fetch failed for {symbol}: {str(e)}")
                        break

                    if df is not None:
                        macd = df['macd'].iloc[-1]
                        macd_signal = df['macd_signal'].iloc[-1]
                        rsi = df['rsi'].iloc[-1]

                        if macd > macd_signal and rsi > 45:
                            trend_reversed = True

                    if ltp >= sl_price:
                        send_telegram(f"🔻 Stop Loss hit for {symbol} at {ltp}")
                        log_message(f"Stop Loss hit for {symbol} at {ltp}")
                        log_trade_csv(symbol, entry_price, ltp, ((ltp-entry_price)/entry_price)*100, "Stop Loss")
                        exit_position(symbol)
                        break

                    elif ltp <= target_price:
                        send_telegram(f"🎯 Target hit for {symbol} at {ltp}")
                        log_message(f"Target hit for {symbol} at {ltp}")
                        log_trade_csv(symbol, entry_price, ltp, ((ltp-entry_price)/entry_price)*100, "Target Hit")
                        exit_position(symbol)
                        break

                    elif not partial_booked and ltp <= partial_target_price:
                        send_telegram(f"📉 Partial profit booked for {symbol} at {ltp:.2f}")
                        log_message(f"Partial target hit for {symbol} at {ltp:.2f}")
                        partial_booked = True

                    elif partial_booked and ltp < target_price:
                        target_price = ltp - 0.5 * atr_sl
                        log_message(f"🎯 Trailing target adjusted after partial profit: {target_price:.2f}")

                    elif ltp <= trailing_trigger and partial_booked:
                        new_sl = ltp * (1 + trailing_sl_pct / 100)
                        if new_sl < sl_price:
                            sl_price = new_sl
                            send_telegram(f"🔁 Trailing SL updated for {symbol} to {sl_price:.2f}")
                            log_message(f"Trailing SL updated to {sl_price:.2f} for {symbol}")

                    if trend_reversed:
                        send_telegram(f"⚠️ Trend Reversal Exit for {symbol} at {ltp:.2f}")
                        log_message(f"Trend reversal detected for {symbol}, exiting position at {ltp:.2f}")
                        log_trade_csv(symbol, entry_price, ltp, ((ltp-entry_price)/entry_price)*100, "Trend Reversal")
                        exit_position(symbol)
                        break

# =======================
# Main Execution
# =======================
if __name__ == '__main__':
    print("Starting Amar's WMA Bearish Multi-Stock Strategy...")
    send_telegram(f"✅ Amar's WMA Bearish Multi-Stock strategy started in {mode.upper()} mode.")
    log_message(f"Amar's WMA Bearish Strategy started in {mode.upper()} mode.")

    while True:
        run_strategy()
        time.sleep(30)
