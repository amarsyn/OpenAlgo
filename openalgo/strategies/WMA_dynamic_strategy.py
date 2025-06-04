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
import threading
from collections import defaultdict
import csv
import statistics
last_trade_time = defaultdict(lambda: datetime.min)
cooldown_seconds = 180  # 3 minutes cooldown
max_bars = 48  # limit future bars to ~4 hours of 5-min candles
positions = defaultdict(lambda: None)

# ==============================
# Setup and Configuration
# ==============================
os.makedirs("logs", exist_ok=True)

api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy_name = "WMA Dynamic Strategy"
symbols = ["TBOTEK","SAREGAMA","ASTERDM"]
exchange = "NSE"
product = "MIS"
quantity = 5
mode = "live"
start_time = "09:15"
end_time = "12:00"
sl_pct = 1.25  
target_pct = 2.5
trailing_sl_pct = 0.5
trailing_trigger_pct = 0.8

# Set backtest range and fixed date for historical evaluation
backtest_start_date = datetime(2025, 3, 1)
backtest_end_date = datetime(2025, 6, 2)

LOG_FILE = f"logs/WMA_dynamic_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/WMA_dynamic_{datetime.now().strftime('%Y-%m-%d')}.csv"

TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

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

def log_backtest(symbol, entry_price, exit_price, direction, result, reason, entry_time, mfe, mae):
    with open(f"logs/backtest_{backtest_end_date.strftime('%Y-%m-%d')}.csv", "a") as f:
        f.write(f"{entry_time},{symbol},{direction},{entry_price},{exit_price},{result:.2f},{reason},{mfe:.2f},{mae:.2f}\n")

# =======================
# MACD Crossover Utility
# =======================
def recent_macd_bullish_cross(df, lookback=3):
    macd = df['macd']
    macd_signal = df['macd_signal']
    for i in range(-lookback - 1, -1):
        if macd.iloc[i - 1] < macd_signal.iloc[i - 1] and macd.iloc[i] > macd_signal.iloc[i]:
            return True
    return False

def recent_macd_bearish_cross(df, lookback=3):
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
    if mode.lower() in ["backtest", "analyze"]:
        start_date = backtest_start_date
        end_date = backtest_end_date
    else:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=1)  # or some default for live mode

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
def detect_trend(df, rsi_bull=51, rsi_bear=49, macd_thresh=0.15, min_atr_pct=0.001):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    log_message(
        f"Checking trend — Close: {last['close']}, WMA: {last['wma']}, Prev_WMA: {prev['wma']}, "
        f"RSI: {last['rsi']}, MACD: {last['macd']}, Signal: {last['macd_signal']}, "
        f"Vol: {last['volume']}, Vol_MA: {last['vol_ma']}, ATR: {last['atr']}"
    )

    # ✅ ATR filter
    min_atr = df['close'].mean() * min_atr_pct
    if last['atr'] < min_atr:
        log_message(f"Filtered out due to low ATR: {last['atr']} < {min_atr}")
        return None

    # ✅ Volume filters
    if last['volume'] < last['vol_ma'] * 0.75:
        log_message(f"Filtered due to weak current volume: {last['volume']} < 75% of MA {last['vol_ma']}")
        return None
    if last['volume'] < last['vol_ma']:
        log_message("⚠️ Very low average volume, skipping for safety.")
        return None

    macd_delta = last['macd'] - last['macd_signal']
    wma_slope = last['wma'] - prev['wma']

    # ✅ Bullish conditions
    if last['rsi'] >= rsi_bull and macd_delta > macd_thresh and wma_slope > 0:
        log_message("Valid Bullish Trend Detected.")
        return "bullish"

    # ✅ Bearish conditions
    elif last['rsi'] <= rsi_bear and macd_delta < -macd_thresh and wma_slope < 0:
        log_message("Valid Bearish Trend Detected.")
        return "bearish"

    # 🚫 No trend matched
    log_message("No valid trend detected — Conditions not met:")
    log_message(f"  RSI: {last['rsi']} (Need ≥{rsi_bull} or ≤{rsi_bear})")
    log_message(f"  MACD Δ: {macd_delta} (Need > {macd_thresh} or < -{macd_thresh})")
    log_message(f"  WMA Slope: {'up' if wma_slope > 0 else 'down' if wma_slope < 0 else 'flat'}")
    return None

# =======================
# Trailing SL Logic
# =======================
def monitor_position(symbol, direction, entry_price, sl, target):
    in_position = True
    trail_triggered = False
    trail_target = None
    exit_price = None
    reason = "Unknown"

    try:
        while in_position:
            time.sleep(60)  # check every minute
            df = fetch_data(symbol)
            if df is None or len(df) < 2:
                continue

            current_price = df['close'].iloc[-1]

            if direction == "bullish":
                if current_price <= sl:
                    exit_price = current_price
                    reason = "Stop Loss Hit"
                    exit_position(symbol, direction)
                    in_position = False
                elif current_price >= target:
                    exit_price = current_price
                    reason = "Target Hit"
                    exit_position(symbol, direction)
                    in_position = False
                elif not trail_triggered and current_price >= entry_price * (1 + trailing_trigger_pct * 0.01):
                    trail_triggered = True
                    trail_target = current_price * (1 - trailing_sl_pct * 0.01)
                    log_message(f"{symbol}: Trailing target activated at {trail_target:.2f}")
                elif trail_triggered and current_price <= trail_target:
                    exit_price = current_price
                    reason = "Trailing Target Hit"
                    exit_position(symbol, direction)
                    in_position = False
                elif trail_triggered:
                    trail_target = max(trail_target, current_price * (1 - trailing_sl_pct * 0.01))

            elif direction == "bearish":
                if current_price >= sl:
                    exit_price = current_price
                    reason = "Stop Loss Hit"
                    exit_position(symbol, direction)
                    in_position = False
                elif current_price <= target:
                    exit_price = current_price
                    reason = "Target Hit"
                    exit_position(symbol, direction)
                    in_position = False
                elif not trail_triggered and current_price <= entry_price * (1 - trailing_trigger_pct * 0.01):
                    trail_triggered = True
                    trail_target = current_price * (1 + trailing_sl_pct * 0.01)
                    log_message(f"{symbol}: Trailing target activated at {trail_target:.2f}")
                elif trail_triggered and current_price >= trail_target:
                    exit_price = current_price
                    reason = "Trailing Target Hit"
                    exit_position(symbol, direction)   # ✅ ADD THIS
                    in_position = False
                elif trail_triggered:
                    trail_target = min(trail_target, current_price * (1 + trailing_sl_pct * 0.01))

    except Exception as e:
        log_message(f"[ERROR] monitor_position failed for {symbol}: {e}")
        reason = f"Monitor Error: {e}"
        exit_price = df['close'].iloc[-1] if 'df' in locals() and not df.empty else entry_price
        positions[symbol] = None

    # Always log exit
    log_trade(symbol, entry_price, exit_price, direction, reason)
    send_telegram(f"{symbol} → {reason} at {exit_price:.2f}")
    positions[symbol] = None

# =====================
# Order Placement
# =====================
def place_order(symbol, direction, entry_price):
    action = "BUY" if direction == "bullish" else "SELL"
    try:
        order_price = round(entry_price, 1)

        if direction == "bearish" and action != "SELL":
            log_message("[ERROR] Action mismatch in bearish trend — forcing SELL")
            action = "SELL"

        response = client.placeorder(
            strategy=strategy_name,
            symbol=symbol,
            action=action,
            exchange=exchange,
            price_type="MARKET",
            product=product,
            quantity=quantity
        )

        if response.get("status") == "success":
            order_id = response.get("orderid")
            log_message(f"Entry Order placed for {symbol} @ {order_price} | Order ID: {order_id}")

            # ➕ SL Order
            sl_resp = place_sl_order(symbol, action, order_price)
            if sl_resp.get("status") != "success":
                log_message(f"[CRITICAL] SL placement failed for {symbol}, exiting trade to avoid risk.")
                exit_position(symbol, direction)
                return None, None  # 🚫 Stop further processing

            # ➕ Target Order
            place_target_order(symbol, action, order_price)

            # ✅ Fetch LTP after successful entry
            ltp = client.quotes(symbol=symbol, exchange=exchange)['data']['ltp']
            return order_id, ltp

        else:
            log_message(f"[ERROR] Failed to place Entry order for {symbol}: {response}")
            return None, None

    except Exception as e:
        log_message(f"[ERROR] Exception in place_order for {symbol}: {str(e)}")
        return None, None

def place_target_order(symbol, entry_action, entry_price):
    try:
        target_price = round(entry_price * (1 + target_pct / 100), 1) if entry_action == "BUY" else round(entry_price * (1 - target_pct / 100), 1)

        target_response = client.placeorder(
            strategy=strategy_name,
            symbol=symbol,
            action="SELL" if entry_action == "BUY" else "BUY",
            exchange=exchange,
            price_type="MARKET",
            product=product,
            quantity=quantity,
            price=target_price
        )

        if target_response.get("status") == "success":
            log_message(f"Target Order placed for {symbol} @ {target_price} | Order ID: {target_response.get('orderid')}")
        else:
            log_message(f"[ERROR] Failed to place Target for {symbol}: {target_response}")
        return target_response

    except Exception as e:
        log_message(f"[ERROR] Exception in Target order for {symbol}: {str(e)}")
        return None

def place_sl_order(symbol, action, entry_price):
    try:
        sl_price = round(entry_price * (1 + sl_pct / 100), 1) if action == "BUY" else round(entry_price * (1 - sl_pct / 100), 1)
        sl_trigger = round(sl_price - 0.5, 1) if action == "BUY" else round(sl_price + 0.5, 1)

        sl_response = client.placeorder(
            strategy=strategy_name,
            symbol=symbol,
            action="SELL" if action == "BUY" else "BUY",
            exchange=exchange,
            price_type="SL-M",  # SL-Market instead of SL-Limit (default in some brokers)
            product=product,
            quantity=quantity,
            trigger_price=sl_trigger  # 🔥 No price needed if SL-M
        )

        if sl_response.get("status") == "success":
            log_message(f"SL Order placed for {symbol} @ {sl_price} (Trigger: {sl_trigger}) | Order ID: {sl_response.get('orderid')}")
        else:
            log_message(f"[ERROR] Failed to place SL for {symbol}: {sl_response}")
        return sl_response

    except Exception as e:
        log_message(f"[ERROR] Exception in place_sl_order for {symbol}: {str(e)}")
        return {"status": "error", "message": str(e)}

def cancel_open_orders(symbol):
    try:
        cancel_response = client.cancel_orders(symbol=symbol, exchange=exchange)
        log_message(f"Cancelled open orders for {symbol}: {cancel_response}")
    except Exception as e:
        log_message(f"[ERROR] Failed to cancel open orders for {symbol}: {e}")

def exit_position(symbol, direction):
    cancel_open_orders(symbol)  # ✅ Cancel SL/Target
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
        positions[symbol] = None
        log_message(f"Exit failed for {symbol}: {str(e)}")
        send_telegram(f"❌ Exit failed for {symbol}: {str(e)}")

# =====================
# Simulate Trade Execution
# =====================
def simulate_trade(df_future, direction, entry, sl, target, max_bars=48):
    if df_future.empty or len(df_future) < 1:
        return entry, "No future data", 0, 0

    # Calculate MFE and MAE once
    if direction == "bullish":
        mfe = df_future['close'].max() - entry
        mae = entry - df_future['close'].min()
    else:
        mfe = entry - df_future['close'].min()
        mae = df_future['close'].max() - entry

    trailing_trigger = target_pct * 0.01
    trailing_sl = trailing_sl_pct * 0.01
    triggered = False
    trail_target = None

    for row in df_future.iloc[:max_bars].itertuples():
        price = row.close

        if direction == "bullish":
            if not triggered and price >= entry * (1 + trailing_trigger):
                triggered = True
                trail_target = price * (1 - trailing_sl)
            elif triggered:
                trail_target = max(trail_target, price * (1 - trailing_sl))

            if price <= sl:
                return price, "Stop Loss", mfe, mae
            if triggered and price <= trail_target:
                return price, "Trailing Exit", mfe, mae
            if price >= target:
                return price, "Target Hit", mfe, mae

        else:  # bearish
            if not triggered and price <= entry * (1 - trailing_trigger):
                triggered = True
                trail_target = price * (1 + trailing_sl)
            elif triggered:
                trail_target = min(trail_target, price * (1 + trailing_sl))

            if price >= sl:
                return price, "Stop Loss", mfe, mae
            if triggered and price >= trail_target:
                return price, "Trailing Exit", mfe, mae
            if price <= target:
                return price, "Target Hit", mfe, mae

    return df_future.iloc[:max_bars].iloc[-1].close, "Exit at end", mfe, mae

# =====================
# Main Strategy Loop with Reversal Support
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

                direction = detect_trend(df[-30:])
                if not direction:
                    log_message("No valid trend detected.")
                    continue
                log_message(f"📊 Trend detected for {symbol}: {direction.upper()}")

                if positions[symbol] == direction:
                    log_message(f"Already in {direction.upper()} trade for {symbol}, skipping...")
                    continue

                if positions[symbol] and positions[symbol] != direction:
                    log_message(f"🔁 Reversing position in {symbol} from {positions[symbol]} to {direction}")
                    exit_position(symbol, positions[symbol])
                    time.sleep(2)

                entry_price = df.iloc[-1]['close']
                atr = df.iloc[-1]['atr']

                if direction == "bullish":
                    sl = entry_price - 1.0 * atr
                    target = entry_price + 3.5 * atr
                elif direction == "bearish":
                    sl = entry_price + 1.0 * atr
                    target = entry_price - 3.5 * atr

                log_message(f"{direction.upper()} Signal -> {symbol} @ {entry_price:.2f} | SL: {sl:.2f}, Target: {target:.2f}")
                send_telegram(f"{direction.upper()} Signal -> {symbol} @ {entry_price:.2f} | SL: {sl:.2f}, Target: {target:.2f}")

                order_id, ltp = place_order(symbol, direction, entry_price)
                if order_id:
                    last_trade_time[symbol] = datetime.now()
                    positions[symbol] = direction

                if order_id:
                    thread = threading.Thread(
                        target=monitor_position,
                        args=(symbol, direction, entry_price, sl, target),
                        daemon=True
                    )
                    thread.start()
                else:
                    log_message(f"Skipping {symbol} due to order failure.")

            time.sleep(120)
        except Exception as e:
            log_message(f"Unexpected error: {str(e)}")
            send_telegram(f"Strategy Error: {str(e)}")
            time.sleep(30)

# =====================
# run_backtest() Function
# =====================
def run_backtest():
    log_message("Starting WMA Backtest...")
    log_message(f"Backtest window: {backtest_start_date} to {backtest_end_date}")

    # ✅ Write header once before processing
    with open(f"logs/backtest_{backtest_end_date.strftime('%Y-%m-%d')}.csv", "w") as f:
        f.write("entry_time,symbol,direction,entry,exit,result,reason,mfe,mae\n")

    for symbol in symbols:
        log_message(f"[BT] Processing {symbol}...")
        df = fetch_data(symbol)
        if df is None or len(df) < 50:
            continue

        for i in range(30, len(df) - 1):
            sub_df = df.iloc[:i+1]
            direction = detect_trend(sub_df)

            if not direction:
                continue

            entry_price = sub_df.iloc[-1]['close']
            atr = sub_df.iloc[-1]['atr']

            if direction == "bullish":
                sl = entry_price - 1.25 * atr
                target = entry_price + 2.5 * atr
            else:
                sl = entry_price + 1.25 * atr
                target = entry_price - 2.5 * atr

            df_future = df.iloc[i+1:]
            if df_future.empty:
                continue

            try:
                result, reason, mfe, mae = simulate_trade(df_future, direction, entry_price, sl, target)
                entry_time = sub_df.index[-1]
                log_backtest(
                    symbol,
                    entry_price,
                    result,
                    direction,
                    ((result - entry_price) / entry_price) * 100,
                    reason,
                    entry_time,
                    mfe,
                    mae
                )

            except Exception as e:
                log_message(f"[BT] Error during simulation at index {i}: {e}")

    summarize_backtest()

def summarize_backtest():
    file_path = f"logs/backtest_{backtest_end_date.strftime('%Y-%m-%d')}.csv"
    try:
        with open(file_path) as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            profits = [float(r[5]) for r in reader if len(r) > 5]
            total = len(profits)
            wins = sum(1 for p in profits if p > 0)
            losses = total - wins
            avg_return = statistics.mean(profits) if profits else 0
            win_rate = wins / total * 100 if total > 0 else 0
            log_message(f"Backtest Summary — Trades: {total}, Wins: {wins}, Losses: {losses}, Win Rate: {win_rate:.1f}%, Avg Return: {avg_return:.2f}%")
    except Exception as e:
        log_message(f"Summary failed: {e}")

# =====================
# Graceful Exit
# =====================
def graceful_exit(sig, frame):
    log_message("WMA_dynamic_Graceful shutdown requested.")
    summarize_live_trades()  # ✅ <-- Add this line
    send_telegram("WMA_dynamic_Strategy stopped gracefully.")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

def summarize_live_trades():
    try:
        with open("trade_log.csv") as f:
            reader = csv.reader(f)
            profits = []
            for row in reader:
                if len(row) >= 5:
                    try:
                        profits.append(float(row[4]))
                    except ValueError:
                        continue
            total = len(profits)
            wins = sum(1 for p in profits if p > 0)
            losses = total - wins
            avg_return = statistics.mean(profits) if profits else 0
            win_rate = wins / total * 100 if total > 0 else 0
            log_message(f"Live Summary — Trades: {total}, Wins: {wins}, Losses: {losses}, Win Rate: {win_rate:.1f}%, Avg Return: {avg_return:.2f}%")
            send_telegram(f"📈 Live Summary — Trades: {total}, Wins: {wins}, Losses: {losses}, Win Rate: {win_rate:.1f}%, Avg: {avg_return:.2f}%")
    except Exception as e:
        log_message(f"Live summary failed: {e}")


# if __name__ == "__main__":
#     try:
#         run_strategy()
#     except Exception as e:
#         log_message(f"Fatal Error: {e}")
#         send_telegram(f"🔥 Fatal Error: {e}")
#         time.sleep(60)

if __name__ == "__main__":
    try:
        if mode.lower() == "live":
            run_strategy()
        elif mode.lower() in ["analyze", "backtest"]:
            run_backtest()
        else:
            log_message(f"Invalid mode: {mode}")
    except Exception as e:
        log_message(f"Fatal Error: {e}")
        send_telegram(f"🔥 Fatal Error: {e}")
        time.sleep(60)
