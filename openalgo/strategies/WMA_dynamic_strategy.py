# ==============================================================================
# Strategy: Weighted MA Dynamic Trend Strategy (Enhanced)
# ------------------------------------------------------------------------------
# Description:
# This intraday strategy identifies directional breakouts using adaptive trend
# and momentum confirmation. Optimized for risk-reward consistency and dynamic
# market responsiveness.
#
# Entry Criteria:
# - 20-period WMA for trend direction.
# - MACD crossover for momentum.
# - RSI using adaptive 50-bar percentile bands:
#     • Bullish: Close > WMA, WMA rising, RSI > 75th percentile, MACD > Signal
#     • Bearish: Close < WMA, WMA falling, RSI < 25th percentile, MACD < Signal
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
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta, date
import time
import sys
import signal
from openalgo import api
import requests
import os
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
strategy_name = "WMA Dynamic Trend Strategy"
symbols = ["CHAMBALFERT","PATANJALI","INFY", "TECHM", "ICICIBANK","RELIANCE","BHARTIARTL"]
exchange = "NSE"
product = "MIS"
quantity = 5
mode = "live"
start_time = "09:20"
end_time = "14:30"
target_pct = 2.4
trailing_sl_pct = 0.3
trailing_trigger_pct = 0.35
trailing_profit_lock_pct = 0.5  # Dynamic profit lock if price falls 0.5% from peak
atr_multiplier = 1.2
LOG_FILE = f"logs/WMA_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/WMA_{datetime.now().strftime('%Y-%m-%d')}.csv"
TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

client = api(api_key=api_key)
trade_count = 0
# max_trades_per_day = 12
# last_trade_time = datetime.now() - timedelta(minutes=15)
today = date.today()

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

def log_message(message):
    timestamped = f"WMA {datetime.now()} - {message}"
    print(timestamped)
    with open(LOG_FILE, "a", encoding="utf-8") as log_file:
        log_file.write(f"{timestamped}\n")

def log_trade_csv(symbol, entry_price, close_price, profit_pct, reason):
    with open(TRADE_LOG, "a", encoding="utf-8") as log_file:
        log_file.write(f"{datetime.now()},{symbol},{entry_price},{close_price},{profit_pct:.2f},{reason},Trailing SL\n")

# =======================
# Data Fetching
# =======================
def fetch_data(symbol, interval="5m"):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=2)
    df = client.history(
        symbol=symbol,
        exchange=exchange,
        interval=interval,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d")
    )

    log_message(f"🔍 Raw response for {symbol}: {df}")

    if not isinstance(df, pd.DataFrame) or df.empty:
        log_message(f"⚠️ No historical data found for {symbol}")
        return None

    df.index = pd.to_datetime(df.index)
    df['wma'] = ta.wma(df['close'], length=20)
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    macd = ta.macd(df['close'])
    df['macd'] = macd.iloc[:, 0]
    df['macd_signal'] = macd.iloc[:, 1]
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    df['vwap'] = (df['volume'] * (df['high'] + df['low'] + df['close']) / 3).cumsum() / df['volume'].cumsum()
    return df


def exit_position(symbol, direction):
    action = "BUY" if direction == "bearish" else "SELL"
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

# =======================
# Entry Condition Logic
# =======================
def check_entry_conditions(df, direction):
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    log_message(f"Checking condition: close={latest['close']}, wma={latest['wma']}, prev_wma={previous['wma']}, rsi={latest['rsi']}, vol={latest['volume']}, vol_ma={latest['vol_ma']}, macd={latest['macd']}, macd_signal={latest['macd_signal']}, atr={df['atr'].iloc[-1]}")

    if df['atr'].iloc[-1] < 1.0:
        log_message("ATR too low, skipping entry.")
        return False

    if latest['volume'] < 0.4 * latest['vol_ma']:
        log_message("Volume too low compared to average.")
        return False

    if direction == "bullish":
        if (
            latest['close'] > latest['wma'] > previous['wma'] and
            latest['macd'] > latest['macd_signal'] and
            latest['rsi'] > 60 and
            latest['close'] > latest['vwap']
        ):
            log_message("Bullish entry condition met.")
            return True
        else:
            log_message("Bullish trend conditions not met.")
            return False

    elif direction == "bearish":
        if (
            latest['close'] < latest['wma'] < previous['wma'] and
            latest['macd'] < latest['macd_signal'] and
            latest['rsi'] < 40 and
            latest['close'] < latest['vwap']
        ):
            log_message("Bearish entry condition met.")
            return True
        else:
            log_message("Bearish trend conditions not met.")
            return False

# =======================
# Cooldown Timer Validation (to be called inside run_strategy)
# =======================
# def is_cooldown_active(last_trade_time):
    # if (datetime.now() - last_trade_time).seconds < 30:
    #     log_message("Cooldown active. Skipping.")
    #     return True
    # return False

# =======================
# Order Management
# =======================
def place_order(symbol, direction):
    action = "SELL" if direction == "bearish" else "BUY"
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
        if response.get("status") != "success":
            log_message(f"❌ Order failed for {symbol}: {response.get('message', 'Unknown error')}")
            return None, None
        return response['orderid'], client.quotes(symbol=symbol, exchange=exchange)['data']['ltp']
    except Exception as e:
        send_telegram(f"Order failed for {symbol}: {str(e)}")
        log_message(f"Order failed for {symbol}: {str(e)}")
        return None, None

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

# =======================
# Market Direction Detection
# =======================
def detect_market_direction(df):
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    if (
        latest['close'] > latest['wma'] and
        latest['wma'] > previous['wma'] and
        latest['rsi'] > 60 and
        latest['macd'] > latest['macd_signal']
    ):
        return "bullish"
    elif (
        latest['close'] < latest['wma'] and
        latest['wma'] < previous['wma'] and
        latest['rsi'] < 40 and
        latest['macd'] < latest['macd_signal']
    ):
        return "bearish"
    else:
        return None

# =======================
# Strategy Execution
# =======================
def run_strategy():
    global trade_count, today, last_trade_time, max_trades_per_day, last_data_timestamp
    last_data_timestamp = {}
    now = datetime.now()

    if date.today() != today:
        trade_count = 0
        today = date.today()

    # if not (start_time <= now.strftime("%H:%M") <= end_time) or trade_count >= max_trades_per_day:
    #     log_message("Outside trading window or max trades reached.")
    #     return

    for symbol in symbols:
        df = fetch_data(symbol)
        if df is None or df.empty:
            log_message(f"No data received for {symbol}, skipping.")
            continue

        direction_detected = detect_market_direction(df)
        if not direction_detected:
            log_message(
                f"[{symbol}] LTP: {df['close'].iloc[-1]:.2f} | Trend: NONE | "
                f"Close vs WMA: {'>' if df['close'].iloc[-1] > df['wma'].iloc[-1] else '<'} | "
                f"WMA Slope: {'↑' if df['wma'].iloc[-1] > df['wma'].iloc[-2] else '↓'} | "
                f"RSI: {df['rsi'].iloc[-1]:.1f} | "
                f"MACD: {df['macd'].iloc[-1]:.2f} vs Signal: {df['macd_signal'].iloc[-1]:.2f}"
            )
            continue
        # if trade_count >= max_trades_per_day:
        #     log_message(f"Max trades reached; skipping remaining symbols.")
        #     break

        # if is_cooldown_active(last_trade_time):
        #     log_message(f"Cooldown active; skipping {symbol}.")
        #     continue

        # Ensure detected direction matches entry logic
        if direction_detected == "bullish" and not check_entry_conditions(df, "bullish"):
            continue
        if direction_detected == "bearish" and not check_entry_conditions(df, "bearish"):
            continue
        
        was_uptrend = detect_market_direction(df.iloc[:-1]) == "bullish"
        is_uptrend = direction_detected == "bullish"
        if was_uptrend != is_uptrend:
            log_message("⚠️ Trend flip detected between last and current candle. Skipping.")
            continue

        latest_index = df.index[-1]
        if last_data_timestamp.get(symbol) == latest_index:
            log_message(f"Duplicate data timestamp for {symbol}, skipping iteration.")
            continue

        last_data_timestamp[symbol] = latest_index

        close_price = df['close'].iloc[-1]

        position = "OPEN" if trade_count > 0 else "NONE"
        log_message(f"Symbol: {symbol} | LTP: {close_price:.2f} | Trend: {'UP' if is_uptrend else 'DOWN'} | Pos: {position} | Was: {'UP' if was_uptrend else 'DOWN'} | VWAP: {df['vwap'].iloc[-1]:.2f}")

        df_htf = df.resample('15min').last().dropna()
        htf_trend = detect_market_direction(df_htf)
        if htf_trend != direction_detected:
            log_message("Higher timeframe trend mismatch.")
            continue

            order_id, entry_price = place_order(symbol, direction_detected)
            if order_id and entry_price:
                atr_value = df['atr'].iloc[-1]
                reward = abs(entry_price * (target_pct / 100))
                risk = atr_value * atr_multiplier
                rr_ratio = reward / risk

                if rr_ratio < 2.0:
                    log_message(f"R:R too low ({rr_ratio:.2f}), skipping trade.")
                    send_telegram(f"⚠️ R:R too low ({rr_ratio:.2f}) for {symbol}, skipping trade.")
                    continue

                trade_count += 1
                last_trade_time = datetime.now()

                send_telegram(f"Order Placed for {symbol}, Order ID: {order_id} at {entry_price}")
                log_message(f"Order Placed for {symbol}, Order ID: {order_id} at {entry_price}")

                max_sl_pct = 0.6
                sl_buffer = min(atr_value * atr_multiplier, entry_price * (max_sl_pct / 100))
                sl_price = entry_price + sl_buffer if direction_detected == "bearish" else entry_price - sl_buffer
                log_message(f"SL for {symbol} set at {sl_price:.2f} (Capped SL Buffer: {sl_buffer:.2f})")

                dynamic_target_pct = df['atr'].iloc[-1] * 2.5 / entry_price * 100
                target_price = entry_price * (1 - dynamic_target_pct / 100) if direction_detected == "bearish" else entry_price * (1 + dynamic_target_pct / 100)
                trailing_trigger = entry_price * (1 - trailing_trigger_pct / 100) if direction_detected == "bearish" else entry_price * (1 + trailing_trigger_pct / 100)

                trade_start = datetime.now()
                max_price_after_entry = entry_price
                trailing_profit_triggered = False
                while True:
                    time.sleep(10)
                    try:
                        quote = client.quotes(symbol=symbol, exchange=exchange)
                        if "data" in quote and "ltp" in quote["data"]:
                            ltp = quote["data"]["ltp"]
                        else:
                            raise ValueError(f"Invalid quote format: {quote}")
                    except Exception as e:
                        log_message(f"Quote fetch failed: {str(e)}")
                        break

                    # Update max price after entry
                    if direction_detected == "bullish":
                        max_price_after_entry = max(max_price_after_entry, ltp)
                    else:
                        max_price_after_entry = min(max_price_after_entry, ltp)

                    # Trigger profit trailing logic if price moved ≥ trailing_trigger_pct
                    move_pct = abs((ltp - entry_price) / entry_price * 100)
                    if not trailing_profit_triggered and move_pct >= trailing_trigger_pct:
                        trailing_profit_triggered = True
                        log_message(f"📈 {symbol} Trailing Profit Activated at {ltp:.2f}")
                        send_telegram(f"📈 {symbol}: Trailing Profit Triggered at {ltp:.2f}")

                    # Exit if price retraced ≥ trailing_profit_lock_pct from peak
                    if trailing_profit_triggered:
                        retrace_pct = abs((max_price_after_entry - ltp) / max_price_after_entry * 100)
                        if retrace_pct >= trailing_profit_lock_pct:
                            pl_pct = ((ltp - entry_price) / entry_price * 100) if direction_detected == "bullish" else ((entry_price - ltp) / entry_price * 100)
                            log_message(f"🔁 {symbol} Exited via Dynamic Profit Lock at {ltp:.2f} | Retraced {retrace_pct:.2f}% from peak {max_price_after_entry:.2f}")
                            send_telegram(f"🔁 {symbol} Dynamic Profit Exit @ {ltp:.2f} | P/L: {pl_pct:.2f}%")
                            log_trade_csv(symbol, entry_price, ltp, pl_pct, "Dynamic Profit Lock")
                            exit_position(symbol, direction_detected)
                            break

                    df_live = fetch_data(symbol)
                    if df_live is not None:
                        latest_live = df_live.iloc[-1]

                        if latest_live['volume'] < 0.05 * latest_live['vol_ma'] or latest_live['volume'] < 1000:
                            log_message("Volume too low.")
                            continue

                        log_message(f"🔁 Reversal Check: RSI={latest_live['rsi']:.2f}, MACD={latest_live['macd']:.2f}, Signal={latest_live['macd_signal']:.2f}")

                        # Candle-based early exit check
                        if latest_live['close'] < latest_live['open'] and (latest_live['close'] - latest_live['low']) < 0.25 * (latest_live['high'] - latest_live['low']):
                            log_message(f"⚠️ Bearish candle detected - early exit for {symbol}.")
                            send_telegram(f"⚠️ {symbol}: Red candle signal, exiting @ {ltp}")
                            pl_pct = ((ltp - entry_price) / entry_price * 100)
                            log_trade_csv(symbol, entry_price, ltp, pl_pct, "Red Candle Exit")
                            exit_position(symbol, direction_detected)
                            break

                        reversal = (
                            (latest_live['rsi'] > 60 and latest_live['macd'] > latest_live['macd_signal']) if direction_detected == "bearish"
                            else (latest_live['rsi'] < 40 and latest_live['macd'] < latest_live['macd_signal'])
                        )

                        if reversal:
                            send_telegram(f"🔁 Reversal detected in {symbol} — exiting @ {ltp}")
                            log_message(f"{symbol} reversal exit @ {ltp}")
                            pl_pct = ((entry_price - ltp) / entry_price * 100) if direction_detected == "bearish" else ((ltp - entry_price) / entry_price * 100)
                            log_trade_csv(symbol, entry_price, ltp, pl_pct, "Reversal Exit")
                            exit_position(symbol, direction_detected)
                            break

                        hit_sl = ltp >= sl_price if direction_detected == "bearish" else ltp <= sl_price
                        hit_target = ltp <= target_price if direction_detected == "bearish" else ltp >= target_price
                        hit_trail = ltp <= trailing_trigger if direction_detected == "bearish" else ltp >= trailing_trigger

                        if hit_sl or hit_target:
                            reason = "Stop Loss" if hit_sl else "Target Hit"
                            send_telegram(f"{'🔻' if hit_sl else '🎯'} {reason} for {symbol} at {ltp}")
                            log_message(f"{reason} for {symbol} at {ltp}")
                            pl_pct = ((entry_price - ltp) / entry_price * 100) if direction_detected == "bearish" else ((ltp - entry_price) / entry_price * 100)
                            if trade_count == 1 and pl_pct > 0:
                                max_trades_per_day = 2
                            log_trade_csv(symbol, entry_price, ltp, pl_pct, reason)
                            exit_position(symbol, direction_detected)
                            trade_duration = datetime.now() - trade_start
                            log_message(f"Trade closed for {symbol}, P/L: {pl_pct:.2f}%, Duration: {trade_duration}")
                            break

                        elif hit_trail:
                            new_sl = ltp * (1 + trailing_sl_pct / 100) if direction_detected == "bullish" else ltp * (1 - trailing_sl_pct / 100)
                            if (direction_detected == "bullish" and new_sl > sl_price) or (direction_detected == "bearish" and new_sl < sl_price):
                                sl_price = new_sl
                                send_telegram(f"🔁 Trailing SL updated for {symbol} to {sl_price:.2f}")
                                log_message(f"Trailing SL updated for {symbol} to {sl_price:.2f}")

# =======================
# Graceful Exit
# =======================
def graceful_exit(signum, frame):
    print("Amar's WMA Dynamic Strategy Graceful shutdown requested... Exiting strategy.")
    log_message("Graceful shutdown invoked.")
    send_telegram("🛑 Amar's WMA Dynamic Strategy stopped gracefully.")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

# =======================
# Main Execution
# =======================
if __name__ == '__main__':
    print("Starting Amar's Weighted MA Dynamic Strategy...")
    send_telegram(f"✅ Amar's Weighted MA Dynamic strategy started in {mode.upper()} mode.")
    log_message(f"Amar's Weighted MA Dynamic Strategy started in {mode.upper()} mode.")
    trade_start = datetime.now()
    pass  # Initialization done inside run_strategy()

    while True:
        run_strategy()
        time.sleep(30)
