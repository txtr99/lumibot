"""
Futures Market Metadata

Centralized metadata for futures contracts including tick sizes, trading fees,
and other market-specific information. This data is used across the portfolio
system to ensure consistent market specifications.

Data sources:
- CME Group contract specifications
- TopStep fee schedules
- Exchange documentation

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

# ==================== FUTURES METADATA ====================

FUTURES_METADATA = {
    # ===== EQUITY INDEX FUTURES =====
    "ES": {
        "name": "E-mini S&P 500",
        "description": "E-mini S&P 500 Index Futures",
        "tick_size": 0.25,
        "tick_value": 12.50,
        "trading_fee": 0.85,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "50 * S&P 500 Index",
        "hours": "Nearly 24 hours",
        "category": "equity_index",
    },
    "MES": {
        "name": "Micro E-mini S&P 500",
        "description": "Micro E-mini S&P 500 Index Futures",
        "tick_size": 0.25,
        "tick_value": 1.25,
        "trading_fee": 0.47,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "5 * S&P 500 Index",
        "hours": "Nearly 24 hours",
        "category": "equity_index",
    },
    "NQ": {
        "name": "E-mini Nasdaq 100",
        "description": "E-mini Nasdaq 100 Index Futures",
        "tick_size": 0.25,
        "tick_value": 5.00,
        "trading_fee": 0.85,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "20 * Nasdaq 100 Index",
        "hours": "Nearly 24 hours",
        "category": "equity_index",
    },
    "MNQ": {
        "name": "Micro E-mini Nasdaq 100",
        "description": "Micro E-mini Nasdaq 100 Index Futures",
        "tick_size": 0.25,
        "tick_value": 0.50,
        "trading_fee": 0.47,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "2 * Nasdaq 100 Index",
        "hours": "Nearly 24 hours",
        "category": "equity_index",
    },
    "YM": {
        "name": "E-mini Dow",
        "description": "E-mini Dow Jones Industrial Average Futures",
        "tick_size": 1.0,
        "tick_value": 5.00,
        "trading_fee": 0.85,
        "exchange": "CBOT",
        "currency": "USD",
        "contract_size": "5 * DJIA",
        "hours": "Nearly 24 hours",
        "category": "equity_index",
    },
    "MYM": {
        "name": "Micro E-mini Dow",
        "description": "Micro E-mini Dow Jones Industrial Average Futures",
        "tick_size": 1.0,
        "tick_value": 0.50,
        "trading_fee": 0.47,
        "exchange": "CBOT",
        "currency": "USD",
        "contract_size": "0.5 * DJIA",
        "hours": "Nearly 24 hours",
        "category": "equity_index",
    },
    "RTY": {
        "name": "E-mini Russell 2000",
        "description": "E-mini Russell 2000 Index Futures",
        "tick_size": 0.10,
        "tick_value": 5.00,
        "trading_fee": 0.85,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "50 * Russell 2000 Index",
        "hours": "Nearly 24 hours",
        "category": "equity_index",
    },
    "M2K": {
        "name": "Micro E-mini Russell 2000",
        "description": "Micro E-mini Russell 2000 Index Futures",
        "tick_size": 0.10,
        "tick_value": 0.50,
        "trading_fee": 0.47,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "5 * Russell 2000 Index",
        "hours": "Nearly 24 hours",
        "category": "equity_index",
    },
    # ===== METALS FUTURES =====
    "GC": {
        "name": "Gold",
        "description": "Gold Futures",
        "tick_size": 0.10,
        "tick_value": 10.00,
        "trading_fee": 0.85,
        "exchange": "COMEX",
        "currency": "USD",
        "contract_size": "100 troy ounces",
        "hours": "Nearly 24 hours",
        "category": "metals",
    },
    "MGC": {
        "name": "Micro Gold",
        "description": "Micro Gold Futures",
        "tick_size": 0.10,
        "tick_value": 1.00,
        "trading_fee": 0.53,
        "exchange": "COMEX",
        "currency": "USD",
        "contract_size": "10 troy ounces",
        "hours": "Nearly 24 hours",
        "category": "metals",
    },
    "SI": {
        "name": "Silver",
        "description": "Silver Futures",
        "tick_size": 0.005,
        "tick_value": 25.00,
        "trading_fee": 0.85,
        "exchange": "COMEX",
        "currency": "USD",
        "contract_size": "5000 troy ounces",
        "hours": "Nearly 24 hours",
        "category": "metals",
    },
    "SIL": {
        "name": "Micro Silver",
        "description": "Micro Silver Futures",
        "tick_size": 0.005,
        "tick_value": 2.50,
        "trading_fee": 0.53,
        "exchange": "COMEX",
        "currency": "USD",
        "contract_size": "500 troy ounces",
        "hours": "Nearly 24 hours",
        "category": "metals",
    },
    # ===== ENERGY FUTURES =====
    "CL": {
        "name": "Crude Oil",
        "description": "Crude Oil WTI Futures",
        "tick_size": 0.01,
        "tick_value": 10.00,
        "trading_fee": 0.85,
        "exchange": "NYMEX",
        "currency": "USD",
        "contract_size": "1000 barrels",
        "hours": "Nearly 24 hours",
        "category": "energy",
    },
    "MCL": {
        "name": "Micro Crude Oil",
        "description": "Micro Crude Oil WTI Futures",
        "tick_size": 0.01,
        "tick_value": 1.00,
        "trading_fee": 0.53,
        "exchange": "NYMEX",
        "currency": "USD",
        "contract_size": "100 barrels",
        "hours": "Nearly 24 hours",
        "category": "energy",
    },
    "NG": {
        "name": "Natural Gas",
        "description": "Natural Gas Futures",
        "tick_size": 0.001,
        "tick_value": 10.00,
        "trading_fee": 0.85,
        "exchange": "NYMEX",
        "currency": "USD",
        "contract_size": "10000 MMBtu",
        "hours": "Nearly 24 hours",
        "category": "energy",
    },
    "QG": {
        "name": "Micro Natural Gas",
        "description": "Micro Natural Gas Futures",
        "tick_size": 0.005,
        "tick_value": 1.25,
        "trading_fee": 0.53,
        "exchange": "NYMEX",
        "currency": "USD",
        "contract_size": "2500 MMBtu",
        "hours": "Nearly 24 hours",
        "category": "energy",
    },
    # ===== TREASURY FUTURES =====
    "ZB": {
        "name": "30-Year T-Bond",
        "description": "30-Year U.S. Treasury Bond Futures",
        "tick_size": 1 / 32,
        "tick_value": 31.25,
        "trading_fee": 0.85,
        "exchange": "CBOT",
        "currency": "USD",
        "contract_size": "$100,000",
        "hours": "Nearly 24 hours",
        "category": "treasury",
    },
    "ZN": {
        "name": "10-Year T-Note",
        "description": "10-Year U.S. Treasury Note Futures",
        "tick_size": 1 / 64,
        "tick_value": 15.625,
        "trading_fee": 0.85,
        "exchange": "CBOT",
        "currency": "USD",
        "contract_size": "$100,000",
        "hours": "Nearly 24 hours",
        "category": "treasury",
    },
    "ZF": {
        "name": "5-Year T-Note",
        "description": "5-Year U.S. Treasury Note Futures",
        "tick_size": 1 / 128,
        "tick_value": 7.8125,
        "trading_fee": 0.85,
        "exchange": "CBOT",
        "currency": "USD",
        "contract_size": "$100,000",
        "hours": "Nearly 24 hours",
        "category": "treasury",
    },
    "ZT": {
        "name": "2-Year T-Note",
        "description": "2-Year U.S. Treasury Note Futures",
        "tick_size": 1 / 128,
        "tick_value": 15.625,
        "trading_fee": 0.85,
        "exchange": "CBOT",
        "currency": "USD",
        "contract_size": "$200,000",
        "hours": "Nearly 24 hours",
        "category": "treasury",
    },
    # ===== CURRENCY FUTURES =====
    "6E": {
        "name": "Euro FX",
        "description": "Euro FX Futures",
        "tick_size": 0.00005,
        "tick_value": 6.25,
        "trading_fee": 0.85,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "€125,000",
        "hours": "Nearly 24 hours",
        "category": "currency",
    },
    "6J": {
        "name": "Japanese Yen",
        "description": "Japanese Yen Futures",
        "tick_size": 0.0000005,
        "tick_value": 6.25,
        "trading_fee": 0.85,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "¥12,500,000",
        "hours": "Nearly 24 hours",
        "category": "currency",
    },
    "6B": {
        "name": "British Pound",
        "description": "British Pound Futures",
        "tick_size": 0.0001,
        "tick_value": 6.25,
        "trading_fee": 0.85,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "£62,500",
        "hours": "Nearly 24 hours",
        "category": "currency",
    },
    "6A": {
        "name": "Australian Dollar",
        "description": "Australian Dollar Futures",
        "tick_size": 0.0001,
        "tick_value": 10.00,
        "trading_fee": 0.85,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "A$100,000",
        "hours": "Nearly 24 hours",
        "category": "currency",
    },
    "6C": {
        "name": "Canadian Dollar",
        "description": "Canadian Dollar Futures",
        "tick_size": 0.00005,
        "tick_value": 5.00,
        "trading_fee": 0.85,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "C$100,000",
        "hours": "Nearly 24 hours",
        "category": "currency",
    },
    "6S": {
        "name": "Swiss Franc",
        "description": "Swiss Franc Futures",
        "tick_size": 0.0001,
        "tick_value": 12.50,
        "trading_fee": 0.85,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "CHF 125,000",
        "hours": "Nearly 24 hours",
        "category": "currency",
    },
}


# ==================== HELPER FUNCTIONS ====================


def get_tick_size(symbol: str) -> float:
    """
    Get the tick size for a futures symbol.

    Args:
        symbol: The futures symbol (e.g., 'ES', 'NQ', 'GC')

    Returns:
        The tick size as a float. Defaults to 0.01 if symbol not found.

    Examples:
        >>> get_tick_size('ES')
        0.25
        >>> get_tick_size('MGC')
        0.1
    """
    return FUTURES_METADATA.get(symbol, {}).get("tick_size", 0.01)


def get_trading_fee(symbol: str) -> float:
    """
    Get the trading fee (per contract, per side) for a futures symbol.

    Args:
        symbol: The futures symbol (e.g., 'ES', 'NQ', 'GC')

    Returns:
        The trading fee in USD. Defaults to 0.85 if symbol not found.

    Examples:
        >>> get_trading_fee('ES')
        0.85
        >>> get_trading_fee('MGC')
        0.53
    """
    return FUTURES_METADATA.get(symbol, {}).get("trading_fee", 0.85)


def get_tick_value(symbol: str) -> float:
    """
    Get the tick value (dollar value of one tick movement) for a symbol.

    Args:
        symbol: The futures symbol (e.g., 'ES', 'NQ', 'GC')

    Returns:
        The tick value in USD. Defaults to 1.0 if symbol not found.

    Examples:
        >>> get_tick_value('ES')
        12.5
        >>> get_tick_value('NQ')
        5.0
    """
    return FUTURES_METADATA.get(symbol, {}).get("tick_value", 1.0)


# Explicit contract multipliers for Topstep-permitted symbols; fallback uses tick_value / tick_size.
MULTIPLIER_OVERRIDES = {
    # Equity index
    "ES": 50.0,
    "MES": 5.0,
    "NQ": 20.0,
    "MNQ": 2.0,
    "RTY": 50.0,
    "M2K": 5.0,
    "YM": 5.0,
    "MYM": 0.5,
    "NKD": 5.0,
    # Crypto micros
    "MBT": 5.0,
    "MET": 0.1,
    # Energy
    "CL": 1000.0,
    "MCL": 100.0,
    "QM": 500.0,
    "NG": 10000.0,
    "QG": 2500.0,
    "MNG": 2500.0,
    "RB": 42000.0,
    "HO": 42000.0,
    # Metals
    "GC": 100.0,
    "MGC": 10.0,
    "SI": 5000.0,
    "SIL": 1000.0,
    "HG": 25000.0,
    "MHG": 12500.0,
    "PL": 50.0,
    # Ags / meats
    "HE": 40000.0,
    "LE": 40000.0,
    "ZC": 50.0,
    "ZW": 50.0,
    "ZS": 50.0,
    "ZM": 100.0,
    "ZL": 600.0,
    # FX
    "6A": 100000.0,
    "6B": 62500.0,
    "6C": 100000.0,
    "6E": 125000.0,
    "6J": 12500000.0,
    "6S": 125000.0,
    "6M": 500000.0,
    "6N": 100000.0,
    "M6A": 10000.0,
    "M6B": 6250.0,
    "M6E": 12500.0,
    "E7": 62500.0,
    # Rates (approximate DV01 scaling; consistent with CME minis)
    "ZT": 2000.0,
    "ZF": 2000.0,
    "ZN": 1000.0,
    "ZB": 1000.0,
    "UB": 1000.0,
    "TN": 1000.0,
}


def get_multiplier(symbol: str) -> float:
    """
    Derive the contract multiplier using tick value / tick size when available.
    Defaults to 1.0 if data is missing.
    """
    if not symbol:
        return 1.0
    symbol = symbol.upper()
    if symbol in MULTIPLIER_OVERRIDES:
        return MULTIPLIER_OVERRIDES[symbol]
    try:
        tv = float(get_tick_value(symbol))
        ts = float(get_tick_size(symbol))
        if ts != 0:
            return tv / ts
    except Exception:
        pass
    return 1.0


def get_market_info(symbol: str) -> dict:
    """
    Get all market metadata for a futures symbol.

    Args:
        symbol: The futures symbol (e.g., 'ES', 'NQ', 'GC')

    Returns:
        Dictionary containing all metadata for the symbol.
        Returns empty dict if symbol not found.

    Examples:
        >>> info = get_market_info('ES')
        >>> info['name']
        'E-mini S&P 500'
        >>> info['exchange']
        'CME'
    """
    return FUTURES_METADATA.get(symbol, {})


def get_all_symbols() -> list:
    """
    Get a list of all available futures symbols.

    Returns:
        List of symbol strings sorted alphabetically.

    Examples:
        >>> symbols = get_all_symbols()
        >>> 'ES' in symbols
        True
    """
    return sorted(FUTURES_METADATA.keys())


def get_symbols_by_category(category: str) -> list:
    """
    Get all symbols in a specific category.

    Args:
        category: The category name ('equity_index', 'metals', 'energy',
                  'treasury', 'currency')

    Returns:
        List of symbols in that category, sorted alphabetically.

    Examples:
        >>> equity_symbols = get_symbols_by_category('equity_index')
        >>> 'ES' in equity_symbols
        True
    """
    return sorted([symbol for symbol, data in FUTURES_METADATA.items() if data.get("category") == category])


def round_to_tick(price: float, symbol: str) -> float:
    """
    Round a price to the nearest valid tick for a given symbol.

    Args:
        price: The price to round
        symbol: The futures symbol (e.g., 'ES', 'NQ', 'GC')

    Returns:
        Price rounded to the nearest tick size.

    Examples:
        >>> round_to_tick(4567.33, 'ES')
        4567.25
        >>> round_to_tick(2023.157, 'MGC')
        2023.1
    """
    tick_size = get_tick_size(symbol)
    return round(price / tick_size) * tick_size


# ==================== VALIDATION ====================

if __name__ == "__main__":
    """Test and display futures metadata."""
    print("=" * 70)
    print("FUTURES METADATA DATABASE")
    print("=" * 70)

    # Display all symbols by category
    categories = set(data.get("category") for data in FUTURES_METADATA.values())

    for category in sorted(categories):
        print(f"\n{category.upper().replace('_', ' ')}:")
        symbols = get_symbols_by_category(category)
        for symbol in symbols:
            info = get_market_info(symbol)
            print(
                f"  {symbol:8} - {info['name']:30} " f"Tick: {info['tick_size']:8.4f}  Fee: ${info['trading_fee']:.2f}"
            )

    print(f"\n{'='*70}")
    print(f"Total symbols: {len(FUTURES_METADATA)}")
    print(f"Categories: {len(categories)}")

    # Test helper functions
    print(f"\n{'='*70}")
    print("HELPER FUNCTION TESTS:")
    print(f"  ES tick size: {get_tick_size('ES')}")
    print(f"  MGC trading fee: ${get_trading_fee('MGC'):.2f}")
    print(f"  NQ tick value: ${get_tick_value('NQ'):.2f}")
    print(f"  Round 4567.33 to ES tick: {round_to_tick(4567.33, 'ES')}")
    print("=" * 70)
