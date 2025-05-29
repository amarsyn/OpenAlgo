# ema_fibo_strategy.py
# Strategy: EMA Crossover Fibonacci Reversal Strategy
# Description:
# This strategy uses EMA(5/20) crossover, 50 EMA rejection, Fibonacci retracement (20-bar high/low), RSI (oversold/overbought),
# and breakout of recent high/low to trigger trades. It includes SL/Target, Telegram alerts, logging, and trend reversal detection
# across multiple symbols using OpenAlgo.

import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from openalgo import api
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
import requests
from requests.exceptions import Timeout
import os
import logging
import signal
import sys
import time

# ================================
# 📁 Setup and Configuration
# ================================
os.makedirs("logs", exist_ok=True)
print("🔁 OpenAlgo Python Bot is running.")

# 🔧 Test if logging works (check file permission/path issues)
with open("test_log.txt", "a") as f:
    f.write("Log test\n")

# Telegram Setup (MOVED ABOVE ALL FUNCTIONS)
TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

# Configuration
api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
api_host = 'http://127.0.0.1:5000'
strategy_name = "ema_fibo_reversal"
sl_pct = 0.02     # 2% Stop Loss
target_pct = 0.05 # 5% Target

symbols = ["ADANIPORTS", "SBIN", "HCLTECH"]
exchange = "NSE"
interval = "5m"
qty = 5

# Logging
LOG_FILE = f"logs/EMA_Fibo_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/EMA_Fibo_{datetime.now().strftime('%Y-%m-%d')}.csv"

# DB Setup
base = declarative_base()
class orderlog(base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime)
    symbol = Column(String)
    action = Column(String)
    entry_price = Column(Float)
    exit_price = Column(Float)
    status = Column(String)
    sl = Column(Float)
    target = Column(Float)

engine = create_engine('sqlite:///ema_fibo_log.db')
base.metadata.create_all(engine)
sessionmaker_ = sessionmaker(bind=engine)

# Utility Functions
client = api(api_key=api_key, host=api_host)

def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        try:
            response = requests.post(url, data=payload)
            if response.status_code == 200:
                log_message("📨 Telegram delivered successfully.")
            else:
                log_message(f"⚠️ Telegram failed: {response.status_code} - {response.text}")
        except Exception as e:
            log_message(f"❌ Telegram exception: {e}")    # Silently fail without printing messy Telegram response

def log_message(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = f"{timestamp} EMA-FIBO - {message}"
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def fib_levels(high, low):
    diff = high - low
    return {
        "0.0": high,
        "0.5": high - 0.5 * diff,
        "0.618": high - 0.618 * diff,
        "1.0": low
    }

# Apply timeout logic & reduce lookback period (e.g., 30 days)
def fetch_history_with_timeout(symbol):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    try:
        return client.history(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d')
        )
    except Timeout:
        log_message(f"⚠️ Timeout occurred while fetching history for {symbol}")
        return None
    except Exception as e:
        log_message(f"❌ Unexpected error for {symbol}: {e}")
        return None

def trend_reversal_logic(df):
    last = df.iloc[-1]
    recent = df.tail(20)
    previous_high = recent['high'].max()
    previous_low = recent['low'].min()

    fib = fib_levels(previous_high, previous_low)
    near_fib = abs(last['close'] - fib['0.618']) / fib['0.618'] < 0.01 or abs(last['close'] - fib['0.5']) / fib['0.5'] < 0.01
    bounce_ema50 = last['low'] > last['ema50'] if last['signal'] == 1 else last['high'] < last['ema50']
    rsi_trigger = last['rsi'] < 30 if last['signal'] == 1 else last['rsi'] > 70
    breakout = last['close'] > previous_high if last['signal'] == 1 else last['close'] < previous_low

    if (near_fib or bounce_ema50 or rsi_trigger) and breakout:
        action = 'buy' if last['signal'] == 1 else 'sell'
        price = last['close']
        sl = price * (1 - sl_pct) if action == 'buy' else price * (1 + sl_pct)
        target = price * (1 + target_pct) if action == 'buy' else price * (1 - target_pct)
        return True, action, price, sl, target
    return False, None, None, None, None

def monitor_open_trades(session):
    open_logs = session.query(orderlog).filter_by(status="open").all()
    for log in open_logs:
        try:
            ltp = client.quotes(symbol=log.symbol, exchange=exchange)['data']['ltp']
            if (log.action == 'buy' and (ltp <= log.sl or ltp >= log.target)) or \
               (log.action == 'sell' and (ltp >= log.sl or ltp <= log.target)):
                exit_action = 'sell' if log.action == 'buy' else 'buy'
                client.placeorder(strategy=strategy_name, symbol=log.symbol, action=exit_action.upper(),
                                  exchange=exchange, price_type="MARKET", product="MIS", quantity=qty)
                reason = 'SL HIT' if (ltp <= log.sl if log.action == 'buy' else ltp >= log.sl) else 'TARGET HIT'
                msg = f"EXIT {log.symbol} @ {ltp:.2f} ({reason})"
                send_telegram(msg)
                log_message(msg)
                log.exit_price = ltp
                log.status = "closed"
                session.commit()
        except Exception as e:
            log_message(f"Error monitoring {log.symbol}: {e}")

def run_strategy():
    for symbol in symbols:
        session = sessionmaker_()
        try:
            df = fetch_history_with_timeout(symbol)
            if df is None:
                continue
            df['ema5'] = ta.ema(df['close'], length=5)
            df['ema20'] = ta.ema(df['close'], length=20)
            df['ema50'] = ta.ema(df['close'], length=50)
            df['rsi'] = ta.rsi(df['close'], length=14)
            df['signal'] = 0
            df.loc[(df['ema5'] > df['ema20']) & (df['ema5'].shift(1) <= df['ema20'].shift(1)), 'signal'] = 1
            df.loc[(df['ema5'] < df['ema20']) & (df['ema5'].shift(1) >= df['ema20'].shift(1)), 'signal'] = -1

            entry_ok, action, price, sl, target = trend_reversal_logic(df)
            if entry_ok:
                client.placeorder(strategy=strategy_name, symbol=symbol, action=action.upper(),
                                  exchange=exchange, price_type="MARKET", product="MIS", quantity=qty)
                msg = f"{action.upper()} {symbol} @ {price:.2f}\nSL: {sl:.2f} | TARGET: {target:.2f}"
                send_telegram(msg)
                log_message(msg)
                session.add(orderlog(timestamp=datetime.now(), symbol=symbol, action=action,
                                      entry_price=price, exit_price=0, status="open", sl=sl, target=target))
                session.commit()

            monitor_open_trades(session)
        except Exception as e:
            log_message(f"❌ Error processing {symbol}: {e}")
        finally:
            session.close()

def graceful_exit(signum, frame):
    print("🚩 Graceful shutdown requested. Exiting strategy.")
    send_telegram("🚩 EMA_Fibo Strategy stopped gracefully.")
    log_message("Graceful shutdown invoked.")
    sys.exit(0)

if __name__ == "__main__":
    print("✅ Starting Amar's EMA_Fibo Strategy...")
    send_telegram("✅ Amar's EMA_Fibo strategy started.")
    log_message("✅ Amar's EMA_Fibo strategy started.")

    signal.signal(signal.SIGINT, graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)

    while True:
        run_strategy()
        time.sleep(300)    # Wait 5 minutes before next execution
