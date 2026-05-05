"""Configuration for the Solana Memecoin Agent."""

import os

HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_API_URL = "https://api.helius.xyz/v0"
HELIUS_DAS_URL = HELIUS_RPC_URL

# Thresholds for rug pull detection
RUG_PULL_THRESHOLDS = {
    "top_holder_concentration_pct": 30.0,
    "mint_authority_enabled": True,
    "freeze_authority_enabled": True,
    "low_liquidity_usd": 5000.0,
    "min_holders": 50,
    "dev_holding_pct": 10.0,
    "token_age_hours_suspicious": 24,
    "large_sell_pct": 5.0,
}

# Known rug patterns
KNOWN_RUG_PROGRAMS = [
    "pumpfun",
    "moonshot",
]

# Risk score weights
RISK_WEIGHTS = {
    "mint_authority": 25,
    "freeze_authority": 15,
    "top_holder_concentration": 20,
    "low_liquidity": 15,
    "low_holders": 10,
    "dev_holding": 15,
}
