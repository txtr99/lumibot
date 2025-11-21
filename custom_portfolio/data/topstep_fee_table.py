"""
TopstepX fee reference (round-turn, flat fees) as of May 12, 2025.

Source: User-provided table (TopstepX NFA & clearing fees RT). Values are already round-turn.
Per-order fee = round_turn_fee / 2, rounded to 2 decimals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class TopstepFeeRecord:
    symbol: str
    category: str
    description: str
    round_turn_fee: float

    @property
    def per_order_fee(self) -> float:
        return round(self.round_turn_fee / 2.0, 2)


RAW_FEES = [
    # CME Equity Futures
    ("ES", "CME Equity Futures", "E-mini S&P 500", 2.80),
    ("MES", "CME Equity Futures", "Micro E-mini S&P 500", 0.74),
    ("NQ", "CME Equity Futures", "E-mini NASDAQ 100", 2.80),
    ("MNQ", "CME Equity Futures", "Micro E-mini NASDAQ 100", 0.74),
    ("RTY", "CME Equity Futures", "E-mini Russell 2000", 2.80),
    ("M2K", "CME Equity Futures", "Micro E-mini Russell 2000", 0.74),
    ("NKD", "CME Equity Futures", "Nikkei", 4.34),
    ("MBT", "CME Equity Futures", "Micro E-mini Bitcoin", 2.34),
    ("MET", "CME Equity Futures", "Micro E-mini Ether", 0.24),
    # CME NYMEX Futures
    ("CL", "CME NYMEX Futures", "Crude Oil", 3.04),
    ("MCL", "CME NYMEX Futures", "Micro Crude Oil", 1.04),
    ("QM", "CME NYMEX Futures", "E-mini Crude Oil", 2.44),
    ("PL", "CME NYMEX Futures", "Platinum", 3.24),
    ("QG", "CME NYMEX Futures", "E-mini Natural Gas", 1.04),
    ("RB", "CME NYMEX Futures", "RBOB Gasoline", 3.04),
    ("HO", "CME NYMEX Futures", "Heating Oil", 3.04),
    ("NG", "CME NYMEX Futures", "Natural Gas", 3.20),
    ("MNG", "CME NYMEX Futures", "Micro Henry Hub Natural Gas", 1.24),
    # CME CBOT Equity Futures
    ("YM", "CME CBOT Equity Futures", "Mini-DOW", 2.80),
    ("MYM", "CME CBOT Equity Futures", "Micro Mini-DOW", 0.74),
    # CME Foreign Exchange Futures
    ("6A", "CME Foreign Exchange Futures", "Australian Dollar", 3.24),
    ("M6A", "CME Foreign Exchange Futures", "Micro AUD/USD", 0.52),
    ("6B", "CME Foreign Exchange Futures", "British Pound", 3.24),
    ("6C", "CME Foreign Exchange Futures", "Canadian Dollar", 3.24),
    ("6E", "CME Foreign Exchange Futures", "Euro FX", 3.24),
    ("M6E", "CME Foreign Exchange Futures", "Micro EUR/USD", 0.52),
    ("6J", "CME Foreign Exchange Futures", "Japanese Yen", 3.24),
    ("6S", "CME Foreign Exchange Futures", "Swiss Franc", 3.24),
    ("E7", "CME Foreign Exchange Futures", "E-mini Euro FX", 1.74),
    ("6M", "CME Foreign Exchange Futures", "Mexican Peso", 3.24),
    ("6N", "CME Foreign Exchange Futures", "New Zealand Dollar", 3.24),
    ("M6B", "CME Foreign Exchange Futures", "Micro GBP/USD", 0.52),
    # CME CBOT Financial/Interest Rate Futures
    ("ZT", "CME CBOT Financial/Interest Rate Futures", "2-Year Note", 1.34),
    ("ZF", "CME CBOT Financial/Interest Rate Futures", "5-Year Note", 1.34),
    ("ZN", "CME CBOT Financial/Interest Rate Futures", "10-Year Note", 1.60),
    ("ZB", "CME CBOT Financial/Interest Rate Futures", "30-Year Bond", 1.78),
    ("UB", "CME CBOT Financial/Interest Rate Futures", "Ultra-Bond", 1.94),
    ("TN", "CME CBOT Financial/Interest Rate Futures", "Ultra-Note", 1.64),
    # CME COMEX Futures
    ("GC", "CME COMEX Futures", "Gold", 3.24),
    ("MGC", "CME COMEX Futures", "Micro Gold", 1.24),
    ("SI", "CME COMEX Futures", "Silver", 3.24),
    ("SIL", "CME COMEX Futures", "Micro Silver", 2.04),
    ("HG", "CME COMEX Futures", "Copper", 3.24),
    ("MHG", "CME COMEX Futures", "Micro Copper", 1.24),
    # CME Agricultural Futures
    ("HE", "CME Agricultural Futures", "Lean Hogs", 4.24),
    ("LE", "CME Agricultural Futures", "Live Cattle", 4.24),
    # CME CBOT Commodity Futures (grains & oilseeds)
    ("ZC", "CME CBOT Commodity Futures", "Corn", 4.30),
    ("ZW", "CME CBOT Commodity Futures", "Wheat", 4.30),
    ("ZS", "CME CBOT Commodity Futures", "Soybeans", 4.30),
    ("ZM", "CME CBOT Commodity Futures", "Soybean Meal", 4.30),
    ("ZL", "CME CBOT Commodity Futures", "Soybean Oil", 4.30),
]


FEE_TABLE: Dict[str, TopstepFeeRecord] = {
    sym.upper(): TopstepFeeRecord(sym.upper(), cat, desc, round(val, 2))
    for sym, cat, desc, val in RAW_FEES
}


def get_topstep_fee(symbol: str) -> Optional[TopstepFeeRecord]:
    """Return the fee record for a symbol, if known."""
    if not symbol:
        return None
    return FEE_TABLE.get(symbol.upper())


def get_per_order_fee(symbol: str) -> Optional[float]:
    """Return per-order (per side) fee for a symbol, if known."""
    rec = get_topstep_fee(symbol)
    if rec is None:
        return None
    return rec.per_order_fee


def get_round_turn_fee(symbol: str) -> Optional[float]:
    """Return round-turn fee for a symbol, if known."""
    rec = get_topstep_fee(symbol)
    if rec is None:
        return None
    return rec.round_turn_fee


def build_trading_fees(symbol: str):
    """
    Build lumibot TradingFee list for the symbol (flat per-order fee).
    Returns [] if symbol is unknown.
    """
    fee = get_per_order_fee(symbol)
    if fee is None:
        return []
    try:
        from lumibot.entities import TradingFee
    except Exception:
        return []
    return [TradingFee(flat_fee=fee)]
