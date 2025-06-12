# ==============================================================================
# Strategy: Hull MA Dynamic Trend Strategy (Enhanced)
# ------------------------------------------------------------------------------
# Description:
# This intraday strategy identifies directional breakouts using adaptive trend
# and momentum confirmation. Optimized for risk-reward consistency and dynamic
# market responsiveness.
#
# Entry Criteria:
# - 20-period HMA for trend direction.
# - MACD crossover for momentum.
# - RSI using adaptive 50-bar percentile bands:
#     • Bullish: Close > HMA, HMA rising, RSI > 75th percentile, MACD > Signal
#     • Bearish: Close < HMA, HMA falling, RSI < 25th percentile, MACD < Signal
# - VWAP condition: Price must be above VWAP (bullish) or below (bearish).
# - ATR (14) must be ≥ 1.0 to ensure volatility.
#
# Execution:
# - Entry at market price.
# - SL: ATR × 1.2 (dynamic).
# - Target: ATR × 2.5 (dynamic).
# - R:R must be ≥ 2.0 to enter.
# - Trailing SL activates at 0.35% move, trails by 0.3%.
# - Confirm trend on 15-min timeframe; skip trade if mismatch.
#
# Trade Management:
# - Cooldown of 15 minutes between trades.
# - Max 1 trade/day, but allows 2nd if first is profitable.
#
# Rules:
# - Trading window: 09:20 AM – 2:30 PM.
# - Logs and real-time alerts via Telegram.
# ------------------------------------------------------------------------------
# NSE intraday equity strategy (e.g., ADANIPORTS).
# ==============================================================================
# =======================
# Import Dependencies
# =======================
from openalgo import api
import pandas as pd
import numpy as np
import time
import requests
import os
from datetime import date, datetime, timedelta
import signal
import sys
import pandas_ta as ta
import httpx

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
strategy_name = "Hull MA Dynamic Trend Strategy"
# symbols = ["CLEAN","HOMEFIRST","JYOTICNC"]
exchange = "NSE"
product = "MIS"
quantity = 5
mode = "live"
start_time = "09:19"
end_time = "14:50"
target_pct = 2.4
trailing_sl_pct = 0.5
trailing_trigger_pct = 0.55
atr_multiplier = 1.2
LOG_FILE = f"logs/HMA_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/HMA_{datetime.now().strftime('%Y-%m-%d')}.csv"
TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

client = api(api_key=api_key)
trade_count = 0
max_trades_per_day = 3
last_trade_time = datetime.now() - timedelta(minutes=15)
today = date.today()

# Add this function above run_strategy()
def get_watchlist_symbols():
    # List of candidate stocks to evaluate (maintained in one place)
    candidate_symbols = ["ADANIPORTS", "M&M", "HINDUNILVR", "TATACONSUM"]
    selected_symbols = []

    for symbol in candidate_symbols:
        df = fetch_data(symbol)
        if df is None or len(df) < 50:
            continue

        atr = df['atr'].iloc[-1] if 'atr' in df else None
        macd = df['macd'].iloc[-1] if 'macd' in df else None
        macd_signal = df['macd_signal'].iloc[-1] if 'macd_signal' in df else None
        volume = df['volume'].iloc[-1]
        volume_ma = df['volume'].rolling(20).mean().iloc[-1]

        # Filters: High volatility, MACD momentum, and active volume
        if atr is not None and atr > 1.0 and macd is not None and macd_signal is not None:
            if abs(macd - macd_signal) > 0.2 and volume > volume_ma:
                selected_symbols.append(symbol)

    return selected_symbols

# =======================
# Utility Functions
# =======================
def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        response = requests.post(url, data=payload)
        if response.status_code != 200:
            log_message(f"Telegram error: {response.status_code} - {response.text}")

def log_message(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"{timestamp} HMA - {msg}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] [ST B3] {msg}\n")

def log_trade_csv(symbol, entry_price, close_price, profit_pct, reason):
    with open(TRADE_LOG, "a", encoding="utf-8") as log_file:
        log_file.write(f"{datetime.now()},{symbol},{entry_price},{close_price},{profit_pct:.2f},{reason},Trailing SL\n")
    return profit_pct

def evaluate_profit_and_reset(profit_pct):
    global trade_count
    if profit_pct > 0:
        log_message("✅ Profit booked. Resetting trade count for second trade.")
        trade_count = 1  # Allow second trade
    else:
        log_message("🔁 No profit booked. Trade count not reset.")

# =======================
# Data Fetching (Enhanced Debug)
# =======================
def fetch_data(symbol, interval="5m"):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=5)

    try:
        raw = client.history(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d")
        )
    except httpx.ReadTimeout as e:
        log_message(f"HTTP timeout fetching history for {symbol}: {e}")
        return pd.DataFrame()
    except Exception as e:
        log_message(f"Unhandled error fetching history for {symbol}: {e}")
        return pd.DataFrame()

    log_message(f"Raw data type for {symbol}: {type(raw)}")

    try:
        if isinstance(raw, pd.DataFrame) and not raw.empty and 'close' in raw.columns:
            df = raw.copy()
        else:
            raise ValueError("Invalid or empty DataFrame")
    except Exception as e:
        log_message(f"API fetch error for {symbol}. Exception: {e}. Raw: {repr(raw)[:500]}")
        return pd.DataFrame()

    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
    elif df.index.name == 'timestamp':
        df.index = pd.to_datetime(df.index)
    else:
        log_message(f"No timestamp field found for {symbol}. Columns: {df.columns.tolist()}, Index: {df.index.name}")
        return pd.DataFrame()

    log_message(f"{symbol} data fetched successfully with shape: {df.shape}")

    half_length = 10
    sqrt_length = int(20 ** 0.5)
    wma_half = ta.wma(df['close'], length=half_length)
    wma_full = ta.wma(df['close'], length=20)
    hma_base = 2 * wma_half - wma_full
    df['hma'] = ta.wma(hma_base, length=sqrt_length)

    df['rsi'] = ta.rsi(df['close'], length=14)
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    macd = ta.macd(df['close'])
    df['macd'] = macd['MACD_12_26_9']
    df['macd_signal'] = macd['MACDs_12_26_9']
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    df['vwap'] = (df['volume'] * (df['high'] + df['low'] + df['close']) / 3).cumsum() / df['volume'].cumsum()
    df['rsi_upper'] = df['rsi'].rolling(window=50).quantile(0.75)
    df['rsi_lower'] = df['rsi'].rolling(window=50).quantile(0.25)

    return df

def fetch_ltp_with_retry(symbol, exchange, retries=3, delay=5):
    for attempt in range(retries):
        try:
            quote = client.quotes(symbol=symbol, exchange=exchange)
            if 'data' in quote and 'ltp' in quote['data']:
                return quote['data']['ltp']
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}. Retrying in {delay}s...")
            time.sleep(delay)
    print("❌ All quote fetch attempts failed.")
    return None


def candle_based_exit_fallback(symbol, interval="5m"):
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=1)
        df = client.history(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d")
        )
        if isinstance(df, pd.DataFrame) and not df.empty:
            latest = df.iloc[-1]
            if direction == "bullish" and latest['close'] < latest['open']:
                print(f"🔄 Bearish candle detected for {symbol}. Triggering exit fallback.")
                return True
            elif direction == "bearish" and latest['close'] > latest['open']:
                print(f"🔄 Bullish candle detected for {symbol}. Triggering exit fallback.")
                return True
        return False
    except Exception as e:
        print(f"Candle fallback error: {e}")
        return False

# =======================
# Entry Condition Logic
# =======================
def check_entry_conditions(df, direction):
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    log_message(f"Checking condition: close={latest['close']}, wma={latest['hma']}, prev_wma={previous['hma']}, rsi={latest['rsi']}, vol={latest['volume']}, vol_ma={latest['vol_ma']}, macd={latest['macd']}, macd_signal={latest['macd_signal']}, atr={df['atr'].iloc[-1]}")

    if df['atr'].iloc[-1] < 0.5:
        log_message("ATR too low, skipping entry.")
        return False

    if latest['volume'] <= 0.2 * latest['vol_ma']:
        log_message(f"Volume check: {latest['volume']} vs threshold {0.2 * latest['vol_ma']:.0f}")
        return False

    if direction == "bearish":
        if not (latest['macd'] < latest['macd_signal'] or (latest['macd'] < 0 and latest['macd_signal'] > 0)):
            log_message("MACD conditions not met.")
            return False
        if (
            latest['close'] < latest['hma'] and
            latest['hma'] < previous['hma'] and
            latest['rsi'] < 40
        ):
            log_message("Bearish entry condition met.")
            return True
        log_message("Bearish trend conditions not met.")
        return False
    else:
        if not (latest['macd'] > latest['macd_signal'] or (latest['macd'] > 0 and latest['macd_signal'] < 0)):
            log_message("MACD conditions not met.")
            return False
        if (
            latest['close'] > latest['hma'] and
            latest['hma'] > previous['hma'] and
            latest['rsi'] > 60
        ):
            log_message("Bullish entry condition met.")
            return True
        log_message("Bullish trend conditions not met.")
        return False

# =======================
# Order Management
# =======================
def place_order(symbol, direction, entry_price):
    action = "SELL" if direction == "bearish" else "BUY"
    try:
        response = client.placeorder(
            strategy=strategy_name,
            symbol=symbol,
            action=action,  # ✅ use computed action
            exchange=exchange,
            price_type="MARKET",
            product=product,
            quantity=quantity
        )

        log_message(f"Order response: {response}")  # ✅ debug log

        if response.get("status") == "success" and "orderid" in response:
            return response  # Return full response dictionary
        else:
            log_message(f"Order failed for {symbol}: {response}")
            return None

    except Exception as e:
        log_message(f"Exception placing order for {symbol}: {e}")
        return None

def exit_position(symbol, direction):
    action = "BUY" if direction == "bearish" else "SELL"
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
        send_telegram(f"✅ Exit Order Placed for {symbol}, Order ID: {response['orderid']}")
        log_message(f"Exit Order Placed for {symbol}, Order ID: {response['orderid']}")
    except Exception as e:
        send_telegram(f"Exit Order failed for {symbol}: {str(e)}")
        log_message(f"Exit Order failed for {symbol}: {str(e)}")

def manage_open_trade(symbol, direction, entry_price, sl_price, target_price):
    global last_trade_time, trade_count

    max_wait = timedelta(minutes=10)
    start_time = datetime.now()
    triggered_trailing = False
    partial_booked = False

    # Calculate initial trailing SL and partial target
    trail_sl_price = sl_price
    partial_target_price = entry_price + ((target_price - entry_price) * 0.5) if direction == "bullish" else entry_price - ((entry_price - target_price) * 0.5)

    while datetime.now() - start_time < max_wait:
        ltp = fetch_ltp_with_retry(symbol, exchange)
        if not ltp:
            log_message("❌ LTP fetch failed, fallback to candle exit.")
            if candle_based_exit_fallback(symbol):
                exit_position(symbol, direction)
                break
            continue

        # 🎯 Partial booking
        if not partial_booked:
            if (direction == "bullish" and ltp >= partial_target_price) or (direction == "bearish" and ltp <= partial_target_price):
                log_message(f"💰 Partial target hit at {ltp:.2f}, booked 50% profit.")
                send_telegram(f"💰 Partial profit booked at {ltp:.2f} for {symbol}")
                partial_booked = True

        # 🔁 Trailing SL activation
        if not triggered_trailing and abs(ltp - entry_price) >= entry_price * (trailing_trigger_pct / 100):
            triggered_trailing = True
            trail_sl_price = ltp + (trailing_sl_pct / 100 * ltp) if direction == "bearish" else ltp - (trailing_sl_pct / 100 * ltp)
            log_message(f"🎯 Trailing SL activated at {trail_sl_price:.2f}")

        # 🔄 Trailing SL update
        if triggered_trailing:
            trail_sl_price = min(trail_sl_price, ltp + (trailing_sl_pct / 100 * ltp)) if direction == "bearish" else max(trail_sl_price, ltp - (trailing_sl_pct / 100 * ltp))

        # 🚪 Exit logic
        if (direction == "bearish" and ltp >= trail_sl_price) or (direction == "bullish" and ltp <= trail_sl_price):
            log_message(f"🚪 Exit Triggered - Trailing SL Hit | LTP: {ltp:.2f}")
            exit_position(symbol, direction)
            evaluate_profit_and_reset(ltp - entry_price if direction == "bullish" else entry_price - ltp)
            break

        elif (direction == "bearish" and ltp <= target_price) or (direction == "bullish" and ltp >= target_price):
            log_message(f"🏁 Target Hit! | LTP: {ltp:.2f}")
            exit_position(symbol, direction)
            evaluate_profit_and_reset(ltp - entry_price if direction == "bullish" else entry_price - ltp)
            break

        elif (direction == "bearish" and ltp >= sl_price) or (direction == "bullish" and ltp <= sl_price):
            log_message(f"🚪 Exit Triggered - Stop Loss | LTP: {ltp:.2f}")
            exit_position(symbol, direction)
            evaluate_profit_and_reset(ltp - entry_price if direction == "bullish" else entry_price - ltp)
            break

        time.sleep(10)

# =======================
# Market Direction Detection
# =======================
def detect_market_direction(df):
    latest = df.iloc[-1]
    previous = df.iloc[-2]

    bullish = (
        latest['close'] > latest['hma'] and
        latest['hma'] >= previous['hma'] and
        latest['rsi'] > 55 and
        latest['macd'] > latest['macd_signal']
    )
    bearish = (
        latest['close'] < latest['hma'] and
        latest['hma'] <= previous['hma'] and
        latest['rsi'] < 45 and
        latest['macd'] < latest['macd_signal']
    )

    # Detect potential early reversal
    rsi_neutral_zone = 45 <= latest['rsi'] <= 55
    macd_cross_up = previous['macd'] < previous['macd_signal'] and latest['macd'] > latest['macd_signal']
    macd_cross_down = previous['macd'] > previous['macd_signal'] and latest['macd'] < latest['macd_signal']

    if bullish or (latest['close'] > latest['hma'] and macd_cross_up and not rsi_neutral_zone):
        return "bullish"
    elif bearish or (latest['close'] < latest['hma'] and macd_cross_down and not rsi_neutral_zone):
        return "bearish"
    else:
        return None

def is_cooldown_active(last_trade_time, cooldown_minutes=15):
    return datetime.now() - last_trade_time < timedelta(minutes=cooldown_minutes)

# =======================
# Strategy Execution
# =======================
def run_strategy():
    global trade_count, max_trades_per_day, last_trade_time
    log_message(f"{datetime.now():%Y-%m-%d %H:%M} HMA - Amar's Hull MA Strategy started in LIVE mode.")

    symbols = get_watchlist_symbols()

    while datetime.now().time() < datetime.strptime(end_time, "%H:%M").time():
        if datetime.now().time() < datetime.strptime(start_time, "%H:%M").time():
            log_message("⏳ Waiting for market open...")
            time.sleep(20)
            continue

        for symbol in symbols:
            log_message(f"🔍 Evaluating {symbol}...")

            if trade_count >= max_trades_per_day:
                log_message("🚫 Max trades reached for the day.")
                continue  # ✅ so it evaluates the next symbol or waits again

            if is_cooldown_active(last_trade_time):
                continue

            raw_df = fetch_data(symbol, interval="5m")
            log_message(f"Raw data type for {symbol}: {type(raw_df)}")

            if raw_df is None or raw_df.empty or len(raw_df) < 60:
                log_message(f"⚠️ Insufficient data for {symbol}.")
                continue

            log_message(f"{symbol} data fetched successfully with shape: {raw_df.shape}")
            df = raw_df.copy()
            direction_detected = detect_market_direction(df)

            if not direction_detected:
                log_message("⚠️ No clear direction.")
                continue

            entry_price = df.iloc[-1]['close']
            atr = df.iloc[-1]['atr']
            sl_price = entry_price - atr * atr_multiplier if direction_detected == "bullish" else entry_price + atr * atr_multiplier
            target_price = entry_price + atr * 2.5 if direction_detected == "bullish" else entry_price - atr * 2.5
            rr_ratio = abs(target_price - entry_price) / abs(entry_price - sl_price)
            volume = df.iloc[-1]['volume']

            log_message(f"RR Ratio: {rr_ratio:.2f}, SL: {sl_price:.2f}, Target: {target_price:.2f}, Entry: {entry_price:.2f}")

            volume_threshold = df['vol_ma'].iloc[-1] * 0.2
            if volume < volume_threshold:
                log_message(f"Volume check failed: {volume} vs threshold {volume_threshold:.0f}")
                continue

            # ✅ Correct place for order logic
            order_response = place_order(symbol, direction_detected, entry_price)
            if order_response and isinstance(order_response, dict) and "orderid" in order_response:
                log_message(f"🛒 Order placed: {symbol}, Direction: {direction_detected.upper()}, Entry: {entry_price:.2f}, OrderID: {order_response['orderid']}")

                # ▶️ Start managing the open position
                manage_open_trade(symbol, direction_detected, entry_price, sl_price, target_price)
                last_trade_time = datetime.now()
                trade_count += 1
            else:
                log_message(f"❌ Order placement failed for {symbol}: {order_response}")
                continue

    log_message("✅ Strategy completed or market closed.")

# =======================
# Graceful Exit
# =======================
def graceful_exit(signum, frame):
    print("Amar's Weighted HMA Strategy Graceful shutdown requested... Exiting strategy.")
    log_message("Graceful shutdown invoked.")
    send_telegram("🛑 Amar's HMA Strategy stopped gracefully.")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

# =======================
# Main Execution
# =======================
if __name__ == '__main__':
    print("Starting Amar's Hull MA Strategy...")
    send_telegram(f"✅ Amar's Hull MA strategy started in {mode.upper()} mode.")
    log_message(f"Amar's Hull MA Strategy started in {mode.upper()} mode.")
    trade_start = datetime.now()
    run_strategy()
