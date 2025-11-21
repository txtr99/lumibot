import pytest

from custom_portfolio.data.futures_metadata import (
    FUTURES_METADATA,
    MULTIPLIER_OVERRIDES,
    get_multiplier,
    get_tick_size,
    get_tick_value,
)

# Symbols we rely on from the Topstep fee table
TOPSTEP_SYMBOLS = [
    "ES",
    "MES",
    "NQ",
    "MNQ",
    "RTY",
    "M2K",
    "NKD",
    "MBT",
    "MET",
    "CL",
    "MCL",
    "QM",
    "PL",
    "QG",
    "RB",
    "HO",
    "NG",
    "MNG",
    "YM",
    "MYM",
    "6A",
    "M6A",
    "6B",
    "6C",
    "6E",
    "M6E",
    "6J",
    "6S",
    "6M",
    "6N",
    "M6B",
    "E7",
    "ZT",
    "ZF",
    "ZN",
    "ZB",
    "UB",
    "TN",
    "GC",
    "MGC",
    "SI",
    "SIL",
    "HG",
    "MHG",
    "HE",
    "LE",
    "ZC",
    "ZW",
    "ZS",
    "ZM",
    "ZL",
]


@pytest.mark.parametrize("symbol", TOPSTEP_SYMBOLS)
def test_multiplier_and_ticks_present(symbol):
    # Ensure tick size/value present (fallback allowed to be default but must be >0)
    ts = get_tick_size(symbol)
    tv = get_tick_value(symbol)
    mult = get_multiplier(symbol)

    assert ts > 0, f"Tick size missing/zero for {symbol}"
    assert tv > 0, f"Tick value missing/zero for {symbol}"
    assert mult > 0, f"Multiplier missing/zero for {symbol}"


def test_overrides_cover_topstep_symbols():
    missing = [s for s in TOPSTEP_SYMBOLS if s not in MULTIPLIER_OVERRIDES]
    assert not missing, f"Missing multiplier overrides for: {missing}"
