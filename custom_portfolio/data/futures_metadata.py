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

# ==================== GLOBEX SYMBOL MAPPING ====================
# CME Globex uses different symbols than common trading symbols.
# Contract ID format: CON.F.US.<GLOBEX_SYMBOL>.<EXPIRY>
# Example: CON.F.US.EP.Z25 (ES December 2025)
#
# This mapping is STATIC - Globex symbols never change, only expiry rolls.

# Globex symbol -> Trading symbol (for parsing contract IDs)
# Discovered via ProjectX API contract_search - these are STATIC mappings
GLOBEX_TO_SYMBOL = {
    # Equity Index
    "EP": "ES",  # E-mini S&P 500
    "ENQ": "NQ",  # E-mini Nasdaq 100
    # Energy
    "CLE": "CL",  # Crude Light
    "MCLE": "MCL",  # Micro Crude
    "NGE": "NG",  # Natural Gas
    "NQG": "QG",  # E-Mini Natural Gas
    "NQM": "QM",  # E-Mini Crude Oil
    # Metals
    "GCE": "GC",  # Gold
    "SIE": "SI",  # Silver
    # Currencies
    "EU6": "6E",  # Euro FX
    "JY6": "6J",  # Japanese Yen
    "MX6": "6M",  # Mexican Peso
    # Treasuries
    "FVA": "ZF",  # 5 Year Treasury
    "TYA": "ZN",  # 10 Year Treasury
    "USA": "ZB",  # 30 Year Treasury
    # Agriculture
    "ZCE": "ZC",  # Corn
    "ZSE": "ZS",  # Soybeans
    "ZWA": "ZW",  # Wheat
    # These match directly (no mapping needed):
    # MES, MNQ, MGC, MNG, MYM, RTY, SIL, YM, M6E
}

# Trading symbol -> Globex symbol (for building contract IDs)
SYMBOL_TO_GLOBEX = {v: k for k, v in GLOBEX_TO_SYMBOL.items()}


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
        "tick_value": 0.5,
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
        "tick_value": 25.0,
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
        "tick_value": 1.0,
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
        "tick_value": 12.5,
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
        "tick_size": 0.00390625,
        "tick_value": 7.8125,
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
        "tick_size": 5e-05,
        "tick_value": 5.0,
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
        "tick_size": 5e-05,
        "tick_value": 6.25,
        "trading_fee": 0.85,
        "exchange": "CME",
        "currency": "USD",
        "contract_size": "CHF 125,000",
        "hours": "Nearly 24 hours",
        "category": "currency",
    },
    "NKD": {
        "name": "Nikkei 225 (Globex)",
        "description": "Nikkei 225 (Globex): December 2025",
        "tick_size": 5.0,
        "tick_value": 25.0,
        "trading_fee": 4.34,
        "exchange": "CME",
        "currency": "USD",
        "category": "equity_index",
        "topstep_round_turn_fee": 4.34,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "MBT": {
        "name": "Micro Bitcoin",
        "description": "Micro Bitcoin: November 2025",
        "tick_size": 5.0,
        "tick_value": 0.5,
        "trading_fee": 2.34,
        "exchange": "CME",
        "currency": "USD",
        "category": "crypto",
        "topstep_round_turn_fee": 2.34,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "MET": {
        "name": "Micro Ether",
        "description": "Micro Ether: November 2025",
        "tick_size": 0.5,
        "tick_value": 0.05,
        "trading_fee": 0.24,
        "exchange": "CME",
        "currency": "USD",
        "category": "crypto",
        "topstep_round_turn_fee": 0.24,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "QM": {
        "name": "E-Mini Crude Oil",
        "description": "E-Mini Crude Oil: January 2026",
        "tick_size": 0.025,
        "tick_value": 12.5,
        "trading_fee": 2.44,
        "exchange": "CME",
        "currency": "USD",
        "category": "energy",
        "topstep_round_turn_fee": 2.44,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "PL": {
        "name": "Platinum (Globex)",
        "description": "Platinum (Globex): January 2026",
        "tick_size": 0.1,
        "tick_value": 5.0,
        "trading_fee": 3.24,
        "exchange": "CME",
        "currency": "USD",
        "category": "metals",
        "topstep_round_turn_fee": 3.24,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "RB": {
        "name": "NY Harbor ULSD",
        "description": "NY Harbor ULSD: January 2026",
        "tick_size": 0.0001,
        "tick_value": 4.2,
        "trading_fee": 3.04,
        "exchange": "CME",
        "currency": "USD",
        "category": "energy",
        "topstep_round_turn_fee": 3.04,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "HO": {
        "name": "Lean Hogs (Globex)",
        "description": "Lean Hogs (Globex): February 2026",
        "tick_size": 0.025,
        "tick_value": 10.0,
        "trading_fee": 3.04,
        "exchange": "CME",
        "currency": "USD",
        "category": "energy",
        "topstep_round_turn_fee": 3.04,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "MNG": {
        "name": "Micro Henry Hub Natural Gas",
        "description": "Micro Henry Hub Natural Gas: January 2026",
        "tick_size": 0.001,
        "tick_value": 1.0,
        "trading_fee": 1.24,
        "exchange": "CME",
        "currency": "USD",
        "category": "energy",
        "topstep_round_turn_fee": 1.24,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "M6A": {
        "name": "E-Micro AUD/USD",
        "description": "E-Micro AUD/USD: December 2025",
        "tick_size": 0.0001,
        "tick_value": 1.0,
        "trading_fee": 0.52,
        "exchange": "CME",
        "currency": "USD",
        "category": "currency",
        "topstep_round_turn_fee": 0.52,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "M6E": {
        "name": "E-Micro EUR/USD",
        "description": "E-Micro EUR/USD: December 2025",
        "tick_size": 0.0001,
        "tick_value": 1.25,
        "trading_fee": 0.52,
        "exchange": "CME",
        "currency": "USD",
        "category": "currency",
        "topstep_round_turn_fee": 0.52,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "E7": {
        "name": "E-mini Euro FX",
        "description": "E-mini Euro FX: December 2025",
        "tick_size": 0.0001,
        "tick_value": 6.25,
        "trading_fee": 1.74,
        "exchange": "CME",
        "currency": "USD",
        "category": "currency",
        "topstep_round_turn_fee": 1.74,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "6M": {
        "name": "Mexican Peso (Globex)",
        "description": "Mexican Peso (Globex): December 2025",
        "tick_size": 1e-05,
        "tick_value": 5.0,
        "trading_fee": 3.24,
        "exchange": "CME",
        "currency": "USD",
        "category": "currency",
        "topstep_round_turn_fee": 3.24,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "6N": {
        "name": "New Zealand Dollar (Globex)",
        "description": "New Zealand Dollar (Globex): December 2025",
        "tick_size": 5e-05,
        "tick_value": 5.0,
        "trading_fee": 3.24,
        "exchange": "CME",
        "currency": "USD",
        "category": "currency",
        "topstep_round_turn_fee": 3.24,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "M6B": {
        "name": "E-Micro GBP/USD",
        "description": "E-Micro GBP/USD: December 2025",
        "tick_size": 0.0001,
        "tick_value": 0.625,
        "trading_fee": 0.52,
        "exchange": "CME",
        "currency": "USD",
        "category": "currency",
        "topstep_round_turn_fee": 0.52,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "UB": {
        "name": "Micro Henry Hub Natural Gas",
        "description": "Micro Henry Hub Natural Gas: January 2026",
        "tick_size": 0.001,
        "tick_value": 1.0,
        "trading_fee": 1.94,
        "exchange": "CME",
        "currency": "USD",
        "category": "treasury",
        "topstep_round_turn_fee": 1.94,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "TN": {
        "name": "Ultra 10yr Treasury Note (Globex)",
        "description": "Ultra 10yr Treasury Note (Globex): December 2025",
        "tick_size": 0.015625,
        "tick_value": 15.625,
        "trading_fee": 1.64,
        "exchange": "CME",
        "currency": "USD",
        "category": "treasury",
        "topstep_round_turn_fee": 1.64,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "HG": {
        "name": "Copper (Globex)",
        "description": "Copper (Globex): March 2026",
        "tick_size": 0.0005,
        "tick_value": 12.5,
        "trading_fee": 3.24,
        "exchange": "CME",
        "currency": "USD",
        "category": "metals",
        "topstep_round_turn_fee": 3.24,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "MHG": {
        "name": "Micro Copper",
        "description": "Micro Copper: March 2026",
        "tick_size": 0.0005,
        "tick_value": 1.25,
        "trading_fee": 1.24,
        "exchange": "CME",
        "currency": "USD",
        "category": "metals",
        "topstep_round_turn_fee": 1.24,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "HE": {
        "name": "Micro Ether",
        "description": "Micro Ether: November 2025",
        "tick_size": 0.5,
        "tick_value": 0.05,
        "trading_fee": 4.24,
        "exchange": "CME",
        "currency": "USD",
        "category": "agriculture",
        "topstep_round_turn_fee": 4.24,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "LE": {
        "name": "Crude Light (Globex)",
        "description": "Crude Light (Globex): January 2026",
        "tick_size": 0.01,
        "tick_value": 10.0,
        "trading_fee": 4.24,
        "exchange": "CME",
        "currency": "USD",
        "category": "agriculture",
        "topstep_round_turn_fee": 4.24,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "ZC": {
        "name": "Corn (Globex)",
        "description": "Corn (Globex): March 2026",
        "tick_size": 0.25,
        "tick_value": 12.5,
        "trading_fee": 4.3,
        "exchange": "CME",
        "currency": "USD",
        "category": "agriculture",
        "topstep_round_turn_fee": 4.3,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "ZW": {
        "name": "Wheat (Globex)",
        "description": "Wheat (Globex): March 2026",
        "tick_size": 0.25,
        "tick_value": 12.5,
        "trading_fee": 4.3,
        "exchange": "CME",
        "currency": "USD",
        "category": "agriculture",
        "topstep_round_turn_fee": 4.3,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "ZS": {
        "name": "Soybeans (Globex)",
        "description": "Soybeans (Globex): January 2026",
        "tick_size": 0.25,
        "tick_value": 12.5,
        "trading_fee": 4.3,
        "exchange": "CME",
        "currency": "USD",
        "category": "agriculture",
        "topstep_round_turn_fee": 4.3,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "ZM": {
        "name": "Soybean Meal (Globex)",
        "description": "Soybean Meal (Globex): January 2026",
        "tick_size": 0.1,
        "tick_value": 10.0,
        "trading_fee": 4.3,
        "exchange": "CME",
        "currency": "USD",
        "category": "agriculture",
        "topstep_round_turn_fee": 4.3,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
    "ZL": {
        "name": "Soybean Oil (Globex)",
        "description": "Soybean Oil (Globex): January 2026",
        "tick_size": 0.01,
        "tick_value": 6.0,
        "trading_fee": 4.3,
        "exchange": "CME",
        "currency": "USD",
        "category": "agriculture",
        "topstep_round_turn_fee": 4.3,
        "last_verified": "2025-11-24",
        "data_source": "projectx_api",
    },
}


# ==================== HELPER FUNCTIONS ====================


def globex_to_symbol(globex: str) -> str:
    """
    Convert Globex symbol to trading symbol.

    Args:
        globex: The Globex symbol from contract ID (e.g., 'EP', 'ENQ', 'GCE')

    Returns:
        The trading symbol (e.g., 'ES', 'NQ', 'GC').
        Returns input unchanged if no mapping exists.

    Examples:
        >>> globex_to_symbol('EP')
        'ES'
        >>> globex_to_symbol('MES')
        'MES'
    """
    return GLOBEX_TO_SYMBOL.get(globex, globex)


def symbol_to_globex(symbol: str) -> str:
    """
    Convert trading symbol to Globex symbol.

    Args:
        symbol: The trading symbol (e.g., 'ES', 'NQ', 'GC')

    Returns:
        The Globex symbol for contract IDs (e.g., 'EP', 'ENQ', 'GCE').
        Returns input unchanged if no mapping exists.

    Examples:
        >>> symbol_to_globex('ES')
        'EP'
        >>> symbol_to_globex('MES')
        'MES'
    """
    return SYMBOL_TO_GLOBEX.get(symbol, symbol)


def contract_id_to_symbol(contract_id: str) -> str:
    """
    Extract trading symbol from a full contract ID.

    Args:
        contract_id: Full contract ID (e.g., 'CON.F.US.EP.Z25')

    Returns:
        The trading symbol (e.g., 'ES').
        Returns the raw Globex part if not in FUTURES_METADATA.

    Examples:
        >>> contract_id_to_symbol('CON.F.US.EP.Z25')
        'ES'
        >>> contract_id_to_symbol('CON.F.US.MES.Z25')
        'MES'
    """
    parts = contract_id.split(".")
    if len(parts) >= 4:
        globex = parts[3]
        symbol = globex_to_symbol(globex)
        # Verify it's a known symbol
        if symbol in FUTURES_METADATA:
            return symbol
        # Check if the globex symbol itself is in metadata
        if globex in FUTURES_METADATA:
            return globex
    # Fallback: return the raw part
    return parts[3] if len(parts) >= 4 else contract_id


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


# TopStepX round-turn fees (NFA & clearing fees) as of May 2025
# Source: TopStep fee schedule - values are round-turn (entry + exit)
# Per-order fee = round_turn_fee / 2
TOPSTEP_ROUND_TURN_FEES = {
    # CME Equity Futures
    "ES": 2.80,
    "MES": 0.74,
    "NQ": 2.80,
    "MNQ": 0.74,
    "RTY": 2.80,
    "M2K": 0.74,
    "NKD": 4.34,
    "MBT": 2.34,
    "MET": 0.24,
    # CME CBOT Equity Futures
    "YM": 2.80,
    "MYM": 0.74,
    # CME NYMEX Futures
    "CL": 3.04,
    "MCL": 1.04,
    "QM": 2.44,
    "PL": 3.24,
    "QG": 1.04,
    "RB": 3.04,
    "HO": 3.04,
    "NG": 3.20,
    "MNG": 1.24,
    # CME Foreign Exchange Futures
    "6A": 3.24,
    "M6A": 0.52,
    "6B": 3.24,
    "M6B": 0.52,
    "6C": 3.24,
    "6E": 3.24,
    "M6E": 0.52,
    "6J": 3.24,
    "6S": 3.24,
    "E7": 1.74,
    "6M": 3.24,
    "6N": 3.24,
    # CME CBOT Financial/Interest Rate Futures
    "ZT": 1.34,
    "ZF": 1.34,
    "ZN": 1.60,
    "ZB": 1.78,
    "UB": 1.94,
    "TN": 1.64,
    # CME COMEX Futures
    "GC": 3.24,
    "MGC": 1.24,
    "SI": 3.24,
    "SIL": 2.04,
    "HG": 3.24,
    "MHG": 1.24,
    # CME Agricultural Futures
    "HE": 4.24,
    "LE": 4.24,
    # CME CBOT Commodity Futures
    "ZC": 4.30,
    "ZW": 4.30,
    "ZS": 4.30,
    "ZM": 4.30,
    "ZL": 4.30,
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
    # Calculate decimal places needed from tick_size (e.g., 0.25 -> 2, 0.1 -> 1)
    tick_str = f"{tick_size:.10f}".rstrip("0")
    decimals = len(tick_str.split(".")[-1]) if "." in tick_str else 0
    # Round to tick, then round again to eliminate floating point noise
    result = round(price / tick_size) * tick_size
    return round(result, decimals)


# ==================== TOPSTEP FEE FUNCTIONS ====================


def get_round_turn_fee(symbol: str) -> float | None:
    """
    Get the TopStepX round-turn fee for a symbol.

    Args:
        symbol: The futures symbol (e.g., 'ES', 'NQ', 'GC')

    Returns:
        Round-turn fee in USD, or None if symbol not found.

    Examples:
        >>> get_round_turn_fee('ES')
        2.80
        >>> get_round_turn_fee('MES')
        0.74
    """
    if not symbol:
        return None
    return TOPSTEP_ROUND_TURN_FEES.get(symbol.upper())


def get_per_order_fee(symbol: str) -> float | None:
    """
    Get the TopStepX per-order (per side) fee for a symbol.

    This is half of the round-turn fee, rounded to 2 decimal places.

    Args:
        symbol: The futures symbol (e.g., 'ES', 'NQ', 'GC')

    Returns:
        Per-order fee in USD, or None if symbol not found.

    Examples:
        >>> get_per_order_fee('ES')
        1.40
        >>> get_per_order_fee('MES')
        0.37
    """
    rt_fee = get_round_turn_fee(symbol)
    if rt_fee is None:
        return None
    return round(rt_fee / 2.0, 2)


def build_trading_fees(symbol: str):
    """
    Build lumibot TradingFee list for a symbol (flat per-order fee).

    Returns empty list if symbol is unknown or TradingFee import fails.

    Args:
        symbol: The futures symbol (e.g., 'ES', 'NQ', 'GC')

    Returns:
        List containing a single TradingFee object, or empty list.
    """
    fee = get_per_order_fee(symbol)
    if fee is None:
        return []
    try:
        from lumibot.entities import TradingFee
    except Exception:
        return []
    return [TradingFee(flat_fee=fee)]


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
