import pandas as pd
import pandas_ta as ta
from datetime import datetime
import os
import signal
import sys
import requests
from openalgo import api

# ================================
# 📁 Setup and Configuration
# ================================
os.makedirs("logs", exist_ok=True)

api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
symbols = ["RAINBOW", "SCHNEIDER", "TRIVENI"]
exchange = "NSE"
interval = "5m"
quantity = 5
rsi_period = 7
bollinger_period = 14
bollinger_std = 2
LIVE_MODE = True
MAX_DRAWDOWN = -300  # Max loss allowed in INR

# ================================
# 📢 Telegram Alert Setup
# ================================
TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"
LOG_FILE = f"logs/MR_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/MR_{datetime.now().strftime('%Y-%m-%d')}.csv"

# ================================
# 🔌 Initialize API
# ================================
try:
    client = api(api_key=api_key, host="http://127.0.0.1:5000")
except Exception as e:
    print(f"❌ API initialization failed: {e}")
    exit(1)

# ================================
# 📢 Alerting and Logging
# ================================
def send_telegram(message):
    if TELEGRAM_ENABLED:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": CHAT_ID, "text": message}
            response = requests.post(url, data=payload)
            if response.ok:
                print(f"📉 Telegram sent: {message}")
            else:
                print(f"❌ Telegram failed: {response.status_code} {response.text}")
        except Exception as e:
            print(f"❌ Telegram alert failed: {e}")

def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    full_message = f"MR {timestamp} - {message}"
    print(full_message)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(full_message + "\n")
    except Exception as e:
        print(f"❌ Failed to write to log file: {e}")

# ================================
# 📊 Strategy Logic (Live Only)
# ================================
def run_mean_reversion_strategy():
    all_trades = []
    total_drawdown = 0

    for symbol in symbols:
        try:
            log_message(f"🔍 Processing {symbol}")
            ltp_data = client.get_ltp(symbol=symbol, exchange=exchange)
            if not ltp_data or "ltp" not in ltp_data:
                raise ValueError(f"No LTP data for {symbol}")

            ltp = float(ltp_data["ltp"])
            candles = client.get_ohlcv(symbol=symbol, exchange=exchange, interval=interval)
            if not candles:
                raise ValueError(f"No OHLCV data for {symbol}")

            df = pd.DataFrame(candles)
            df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.set_index("timestamp", inplace=True)

            df["rsi"] = ta.rsi(df["close"], length=rsi_period)
            bb = ta.bbands(df["close"], length=bollinger_period, std=bollinger_std)
            df["bb_l"] = bb[f"BBL_{bollinger_period}_{bollinger_std}.0"]
            df["bb_u"] = bb[f"BBU_{bollinger_period}_{bollinger_std}.0"]
            df["sma"] = ta.sma(df["close"], length=14)

            trades = []
            position = 0
            entry_price = 0
            stop_loss = 0
            target = 0
            partial_target = 0
            partial_booked = False

            for time, row in df.iterrows():
                if row.isnull().any():
                    continue

                close = row["close"]
                rsi = row["rsi"]
                lower_band = row["bb_l"]
                upper_band = row["bb_u"]
                sma = row["sma"]

                if position == 0:
                    if close <= lower_band * 1.02 and rsi < 50:
                        position = quantity
                        entry_price = close
                        partial_booked = False
                        stop_loss = entry_price * 0.99
                        target = entry_price * 1.02
                        partial_target = entry_price * 1.01
                        if LIVE_MODE:
                            order_id = client.place_order(symbol=symbol, exchange=exchange, action="BUY", quantity=quantity, product_type="MIS", price_type="MARKET")
                            log_message(f"BUY Order Placed for {symbol} at {close:.2f}, Order ID: {order_id}")
                        trades.append([symbol, time, "BUY", close, None])
                        send_telegram(f"📈 BUY signal for {symbol} at {close:.2f} | RSI: {rsi:.2f}")

                    elif close > upper_band and rsi > 65:
                        position = -quantity
                        entry_price = close
                        partial_booked = False
                        stop_loss = entry_price * 1.01
                        target = entry_price * 0.98
                        partial_target = entry_price * 0.99
                        if LIVE_MODE:
                            order_id = client.place_order(symbol=symbol, exchange=exchange, action="SELL", quantity=quantity, product_type="MIS", price_type="MARKET")
                            log_message(f"SELL Order Placed for {symbol} at {close:.2f}, Order ID: {order_id}")
                        trades.append([symbol, time, "SELL", close, None])
                        send_telegram(f"📉 SELL signal for {symbol} at {close:.2f} | RSI: {rsi:.2f}")

                elif position > 0:
                    if close <= stop_loss or close >= target or close >= sma:
                        reason = "SL Hit" if close <= stop_loss else "Target Hit" if close >= target else "SMA Exit"
                        pnl = ((close - entry_price) / entry_price) * 100
                        total_drawdown += pnl * quantity
                        trades.append([symbol, time, "EXIT", close, pnl])
                        if LIVE_MODE:
                            exit_id = client.place_order(symbol=symbol, exchange=exchange, action="SELL", quantity=quantity, product_type="MIS", price_type="MARKET")
                            log_message(f"Exit Order Placed for {symbol} at {close:.2f}, Exit ID: {exit_id}")
                        send_telegram(f"✅ {reason}. EXIT long {symbol} at {close:.2f} | PnL: {pnl:.2f}%")
                        position = 0

                    elif not partial_booked and close >= partial_target:
                        send_telegram(f"📈 Partial Profit Booked for {symbol} at {close:.2f}")
                        log_message(f"Partial profit booked at {close:.2f}")
                        partial_booked = True

                elif position < 0:
                    if close >= stop_loss or close <= target or close <= sma:
                        reason = "SL Hit" if close >= stop_loss else "Target Hit" if close <= target else "SMA Exit"
                        pnl = ((entry_price - close) / entry_price) * 100
                        total_drawdown += pnl * quantity
                        trades.append([symbol, time, "EXIT", close, pnl])
                        if LIVE_MODE:
                            exit_id = client.place_order(symbol=symbol, exchange=exchange, action="BUY", quantity=quantity, product_type="MIS", price_type="MARKET")
                            log_message(f"Exit Order Placed for {symbol} at {close:.2f}, Exit ID: {exit_id}")
                        send_telegram(f"✅ {reason}. EXIT short {symbol} at {close:.2f} | PnL: {pnl:.2f}%")
                        position = 0

                    elif not partial_booked and close <= partial_target:
                        send_telegram(f"📈 Partial Profit Booked for {symbol} at {close:.2f}")
                        log_message(f"Partial profit booked at {close:.2f}")
                        partial_booked = True

            if total_drawdown <= MAX_DRAWDOWN:
                all_trades.extend(trades)
            else:
                log_message(f"🚫 Max drawdown limit reached: ₹{total_drawdown}. Strategy halted.")
                send_telegram(f"🚫 Max drawdown limit reached: ₹{total_drawdown}. Strategy halted.")
                break

        except Exception as e:
            log_message(f"❌ Error processing {symbol}: {e}")
            send_telegram(f"❌ Error processing {symbol}: {e}")

    if all_trades:
        df_trades = pd.DataFrame(all_trades, columns=["Symbol", "Timestamp", "Action", "Price", "PnL%"])
        df_trades.to_csv(TRADE_LOG, index=False)
        print(df_trades)

        if not df_trades[df_trades['Action'] == "EXIT"].empty:
            pnl_data = df_trades[df_trades['Action'] == "EXIT"]
            avg_pnl = pnl_data['PnL%'].astype(float).mean()
            win_rate = (pnl_data['PnL%'].astype(float) > 0).mean() * 100
            print("\n--- Strategy Summary ---")
            print("Total Trades:", len(pnl_data))
            print("Average PnL%:", round(avg_pnl, 2))
            print("Win Rate:", round(win_rate, 2), "%")

# ================================
# 🥩 Exit Handling
# ================================
if __name__ == "__main__":
    def graceful_exit(signum, frame):
        send_telegram("🔕 Amar's Mean Reversion Strategy stopped.")
        log_message("Amar's Mean Reversion Strategy Graceful exit requested.")
        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)

    log_message("📈 Starting Mean Reversion BB + RSI Strategy")
    send_telegram("📈 Starting Mean Reversion BB + RSI Strategy")
    run_mean_reversion_strategy()
