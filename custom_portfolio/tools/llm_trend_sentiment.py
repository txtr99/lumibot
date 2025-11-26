"""
LLM Trend Sentiment

Lightweight tool to fetch short-term market vibe (trend + volatility) from
Databento data or simulated data. Intended for quick scratch-pad usage.

CLI:
    python -m custom_portfolio.tools.llm_trend_sentiment
    python -m custom_portfolio.tools.llm_trend_sentiment --sim
"""

from __future__ import annotations

import argparse
import os
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd
import plotext as plt

warnings.filterwarnings("ignore")

try:
    import databento as db

    HAS_DATABENTO = True
except ImportError:
    HAS_DATABENTO = False

# =============================================================================
# CONFIG
# =============================================================================

ENV_API_KEY = "DATABENTO_API_KEY"
DEFAULT_LOOKBACK_MINUTES = 240  # ~4 hours at 1-minute bars
ENV_FILES = [".env", "env", "env.local"]

SYMBOLS = {
    # Continuous front contracts only; single call, no fallbacks.
    "MES": "MES.c.0",
    "MNQ": "MNQ.c.0",
    "MGC": "MGC.c.0",
}

VIBE_MATRIX = {
    ("up", "high"): "surging",
    ("up", "med"): "climbing",
    ("up", "low"): "calm rise",
    ("flat", "high"): "choppy",
    ("flat", "med"): "ranging",
    ("flat", "low"): "quiet",
    ("down", "high"): "crashing",
    ("down", "med"): "sliding",
    ("down", "low"): "drifting",
}


# =============================================================================
# INDICATORS
# =============================================================================


def calc_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()


def calc_atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, length: int = 14) -> pd.Series:
    tr1 = highs - lows
    tr2 = (highs - closes.shift(1)).abs()
    tr3 = (lows - closes.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def calc_atr_percent(highs: pd.Series, lows: pd.Series, closes: pd.Series, length: int = 14) -> pd.Series:
    atr = calc_atr(highs, lows, closes, length)
    return (atr / closes) * 100


def calc_bb_width(closes: pd.Series, length: int = 20, std: int = 2) -> pd.Series:
    sma = closes.rolling(length).mean()
    std_dev = closes.rolling(length).std()
    width = (std_dev * std * 2) / sma * 100
    return width


def load_databento_key() -> Optional[str]:
    """Load Databento API key from environment or local env files without logging secrets."""
    key = os.getenv(ENV_API_KEY)
    if key:
        return key

    repo_root = Path(__file__).resolve().parents[2]
    for env_name in ENV_FILES:
        env_path = repo_root / env_name
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text().splitlines():
                if not line or line.lstrip().startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == ENV_API_KEY:
                    return v.strip().strip('"').strip("'")
        except Exception:
            continue
    return None


# =============================================================================
# TREND + VOL
# =============================================================================


def detect_trend(df: pd.DataFrame) -> str:
    closes = df["close"]
    price = closes.iloc[-1]

    sma20 = calc_sma(closes, 20)
    sma50 = calc_sma(closes, 50)

    sma20_now = sma20.iloc[-1]
    sma50_now = sma50.iloc[-1]
    sma20_5ago = sma20.iloc[-6]
    sma50_10ago = sma50.iloc[-11]

    above_sma20 = price > sma20_now
    above_sma50 = price > sma50_now

    sma20_rising = sma20_now > sma20_5ago
    sma50_rising = sma50_now > sma50_10ago

    roc_20 = (price - closes.iloc[-21]) / closes.iloc[-21] * 100

    score = 0
    score += 1 if above_sma20 else -1
    score += 1 if above_sma50 else -1
    score += 1 if sma20_rising else -1
    score += 0.5 if sma50_rising else -0.5

    if roc_20 > 0.5:
        score += 1
    elif roc_20 < -0.5:
        score -= 1

    if score >= 2:
        return "up"
    if score <= -2:
        return "down"
    return "flat"


def detect_volatility(df: pd.DataFrame) -> str:
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]

    # Reuse tail slices to avoid redundant slicing on larger dataframes.
    closes_tail = closes.iloc[-60:]
    highs_tail = highs.iloc[-60:]
    lows_tail = lows.iloc[-60:]

    atr_pct = calc_atr_percent(highs_tail, lows_tail, closes_tail, 14)
    atr_now = atr_pct.iloc[-1]
    atr_mean = atr_pct.iloc[-50:].mean()
    atr_std = atr_pct.iloc[-50:].std()

    bb_width = calc_bb_width(closes_tail, 20, 2)
    bb_now = bb_width.iloc[-1]
    bb_mean = bb_width.iloc[-50:].mean()

    highs_last5 = highs_tail.iloc[-5:]
    lows_last5 = lows_tail.iloc[-5:]
    recent_range = (highs_last5.max() - lows_last5.min()) / closes_tail.iloc[-1] * 100

    highs_last50 = highs_tail.iloc[-50:]
    lows_last50 = lows_tail.iloc[-50:]
    closes_anchor = closes_tail.iloc[-25] if len(closes_tail) >= 25 else closes_tail.iloc[0]
    avg_range = (highs_last50.max() - lows_last50.min()) / closes_anchor * 100
    range_ratio = recent_range / (avg_range / 10) if avg_range > 0 else 1

    score = 0

    if atr_now > atr_mean + atr_std:
        score += 2
    elif atr_now > atr_mean:
        score += 1
    elif atr_now < atr_mean - atr_std:
        score -= 2
    else:
        score -= 1

    if bb_now > bb_mean * 1.2:
        score += 1
    elif bb_now < bb_mean * 0.8:
        score -= 1

    if range_ratio > 1.25:
        score += 0.5
    elif range_ratio < 0.8:
        score -= 0.5

    if score >= 2:
        return "high"
    if score <= -1:
        return "low"
    return "med"


def get_vibe(trend: str, volatility: str) -> str:
    return VIBE_MATRIX.get((trend, volatility), "unknown")


def get_market_vibe(df: pd.DataFrame) -> Dict[str, str]:
    if df is None or len(df) < 60:
        return {"trend": "?", "vol": "?", "vibe": "no data"}

    trend = detect_trend(df)
    vol = detect_volatility(df)
    vibe = get_vibe(trend, vol)
    return {"trend": trend, "vol": vol, "vibe": vibe}


# =============================================================================
# DATA FETCHING
# =============================================================================


def fetch_data(
    symbol_key: str,
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
    api_key: Optional[str] = None,
    latency_minutes: int = 10,
    dataset: str = "GLBX.MDP3",
    schema: str = "ohlcv-1m",
) -> Optional[pd.DataFrame]:
    if not HAS_DATABENTO:
        print("(databento not installed)", end=" ")
        return None

    key = api_key or load_databento_key()
    if not key:
        print(f"({ENV_API_KEY} not set)", end=" ")
        return None

    client = db.Historical(key)

    end_default = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=max(latency_minutes, 0))
    start_default = end_default - timedelta(minutes=lookback_minutes)

    symbol_code = SYMBOLS.get(symbol_key, symbol_key)
    stype_in = "continuous" if ".c." in symbol_code else "raw_symbol"

    attempts = 0
    data = None
    cur_end = end_default
    cur_start = start_default

    while attempts < 2:
        attempts += 1
        try:
            data = client.timeseries.get_range(
                dataset=dataset,
                symbols=[symbol_code],
                schema=schema,
                stype_in=stype_in,
                start=cur_start.strftime("%Y-%m-%dT%H:%M:%S"),
                end=cur_end.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            break
        except Exception as exc:
            msg = str(exc)
            if "data_end_after_available_end" in msg and attempts == 1:
                # Back off end time and retry once
                cur_end = cur_end - timedelta(minutes=15)
                cur_start = cur_end - timedelta(minutes=lookback_minutes)
                continue
            print(
                f"error fetching {symbol_key} ({symbol_code}, dataset={dataset}, schema={schema}, "
                f"stype_in={stype_in}): {exc}"
            )
            return None

    if data is None:
        return None

    df = data.to_df().reset_index()
    df.columns = [c.lower() for c in df.columns]

    price_cols = ["open", "high", "low", "close"]
    for col in price_cols:
        if col in df.columns and df[col].mean() > 100000:
            df[col] = df[col] / 1e9

    return df


# =============================================================================
# SIMULATION + OUTPUT
# =============================================================================


def simulate_data(symbol: str, trend_bias: str = "up", vol_level: str = "med", seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)

    base_prices = {"MES": 6800, "MNQ": 25200, "MGC": 4160}
    base = base_prices.get(symbol, 1000)
    drift = {"up": 0.0002, "flat": 0, "down": -0.0002}[trend_bias]
    vol_scale = {"high": 0.003, "med": 0.0015, "low": 0.0007}[vol_level]

    n_bars = max(DEFAULT_LOOKBACK_MINUTES, 400)
    returns = np.random.normal(drift, vol_scale, n_bars)
    prices = base * np.cumprod(1 + returns)

    df = pd.DataFrame(
        {
            "close": prices,
            "open": prices * (1 + np.random.normal(0, vol_scale / 2, n_bars)),
            "high": prices * (1 + np.abs(np.random.normal(0, vol_scale, n_bars))),
            "low": prices * (1 - np.abs(np.random.normal(0, vol_scale, n_bars))),
            "volume": np.random.randint(100, 1000, n_bars),
        }
    )
    return df


def print_vibes(vibes: Dict[str, Dict[str, str]], header: str) -> None:
    print()
    print("=" * 60)
    print(header)
    print("=" * 60)
    print()
    print("┌────────┬────────┬──────┬─────────────┐")
    print("│ Symbol │ Trend  │ Vol  │ Vibe        │")
    print("├────────┼────────┼──────┼─────────────┤")

    for symbol in SYMBOLS:
        v = vibes.get(symbol, {"trend": "?", "vol": "?", "vibe": "?"})
        trend = v["trend"].center(6)
        vol = v["vol"].center(4)
        vibe = v["vibe"].ljust(11)
        print(f"│ {symbol}    │ {trend} │ {vol} │ {vibe} │")

    print("└────────┴────────┴──────┴─────────────┘")
    print()


def plot_closes(symbols: Iterable[str], dfs: Dict[str, Optional[pd.DataFrame]], header: str) -> None:
    """Render compact subplots (one per symbol) with a single show for perf."""
    valid = []
    for symbol in symbols:
        df = dfs.get(symbol)
        if df is None or df.empty or "close" not in df.columns:
            continue
        valid.append((symbol, df["close"].tail(240)))

    if not valid:
        return

    plt.clear_figure()
    plt.clear_data()
    # Fixed compact size; height set to 45 per request
    try:
        plt.plotsize(60, 45)
    except Exception:
        pass
    # Dark-ish palette; ignore failures
    try:
        plt.canvas_color("black")
        plt.axes_color("black")
        plt.ticks_color("white")
    except Exception:
        pass

    plt.subplots(len(valid), 1)
    for idx, (symbol, tail) in enumerate(valid, start=1):
        plt.subplot(idx, 1)
        plt.title(symbol)
        plt.xlabel("bars (old → new)")
        plt.ylabel("close")
        # Pad y-limits so the line isn’t hugging the frame
        try:
            ymin, ymax = float(tail.min()), float(tail.max())
            span = ymax - ymin if ymax != ymin else max(abs(ymax), 1.0)
            pad = span * 0.05
            plt.ylim(ymin - pad, ymax + pad)
        except Exception:
            pass
        try:
            plt.plot(tail.values.tolist(), label=symbol, color="cyan")
        except Exception:
            plt.plot(tail.values.tolist(), label=symbol)

    plt.show()


def build_vibes(
    symbols: Iterable[str],
    lookback_minutes: int,
    use_sim: bool,
    api_key: Optional[str] = None,
    latency_minutes: int = 10,
    dataset: str = "GLBX.MDP3",
    schema: str = "ohlcv-1m",
) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Optional[pd.DataFrame]]]:
    vibes: Dict[str, Dict[str, str]] = {}
    results: Dict[str, Dict[str, str]] = {}
    data_frames: Dict[str, Optional[pd.DataFrame]] = {}
    symbol_list = list(symbols)
    statuses: Dict[str, str] = {}
    databento_key = None if use_sim else (api_key or load_databento_key())
    if not databento_key and not use_sim:
        print(f"{ENV_API_KEY} not set; set it via env or an env file ({', '.join(ENV_FILES)})")

    def worker(symbol: str):
        if use_sim:
            df_local = simulate_data(symbol)
            status_local = f"{len(df_local)} bars (sim) ✓"
        else:
            if not databento_key:
                return symbol, None, f"FAILED ({ENV_API_KEY} missing)"
            df_local = fetch_data(
                symbol,
                lookback_minutes=lookback_minutes,
                api_key=databento_key,
                latency_minutes=latency_minutes,
                dataset=dataset,
                schema=schema,
            )
            if df_local is not None and len(df_local) > 60:
                status_local = f"{len(df_local)} bars ✓"
            else:
                status_local = "FAILED"
        return symbol, df_local, status_local

    max_workers = min(4, len(symbol_list)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(worker, sym): sym for sym in symbol_list}
        for future in as_completed(future_map):
            symbol, df_local, status_local = future.result()
            statuses[symbol] = status_local
            data_frames[symbol] = df_local
            results[symbol] = get_market_vibe(df_local)

    for symbol in symbol_list:
        status = statuses.get(symbol, "FAILED")
        print(f"Fetching {symbol}... {status}")

    return results, data_frames


# =============================================================================
# CLI
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM Trend Sentiment via Databento")
    parser.add_argument(
        "--symbols", nargs="+", default=list(SYMBOLS.keys()), help="Symbols to fetch (default: MES MNQ MGC)"
    )
    parser.add_argument("--hours", type=int, default=4, help="Lookback hours (default: 4)")
    parser.add_argument("--minutes", type=int, default=None, help="Lookback minutes (overrides --hours)")
    parser.add_argument(
        "--latency-minutes",
        type=int,
        default=10,
        help="Back off end time by this many minutes to avoid requesting beyond available data",
    )
    parser.add_argument("--dataset", type=str, default="GLBX.MDP3", help="Databento dataset (default: GLBX.MDP3)")
    parser.add_argument("--schema", type=str, default="ohlcv-1m", help="Databento schema (default: ohlcv-1m)")
    parser.add_argument("--plot", action="store_true", help="Plot close prices to console using plotext")
    parser.add_argument("--sim", action="store_true", help="Use simulated data instead of Databento")
    return parser.parse_args()


def main() -> Dict[str, Dict[str, str]]:
    args = parse_args()
    lookback_minutes = args.minutes or args.hours * 60
    header_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    header = (
        f"LLM TREND SENTIMENT — {header_time} — lookback {lookback_minutes}m "
        f"(latency {args.latency_minutes}m, dataset {args.dataset}, schema {args.schema})"
    )

    api_key = None if args.sim else load_databento_key()
    vibes, data_frames = build_vibes(
        args.symbols,
        lookback_minutes,
        args.sim,
        api_key=api_key,
        latency_minutes=args.latency_minutes,
        dataset=args.dataset,
        schema=args.schema,
    )
    print_vibes(vibes, header)

    print("Raw output:")
    for symbol, vibe in vibes.items():
        print(f"  {symbol}: {vibe}")

    if args.plot:
        try:
            plot_closes(args.symbols, data_frames, header)
        except Exception as exc:
            print(f"(plot failed: {exc})")
    return vibes


if __name__ == "__main__":
    main()
