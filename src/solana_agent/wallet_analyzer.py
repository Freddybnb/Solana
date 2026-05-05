"""Wallet and dev analysis module - track dev wallets, linked wallets, transaction patterns."""

from __future__ import annotations

from dataclasses import dataclass, field

from solana_agent.helius_client import HeliusClient


@dataclass
class WalletProfile:
    address: str = ""
    sol_balance: float = 0.0
    token_count: int = 0
    tokens: list[dict] = field(default_factory=list)
    recent_transactions: list[dict] = field(default_factory=list)
    total_tx_count: int = 0
    # Dev analysis
    is_suspected_dev: bool = False
    dev_signals: list[str] = field(default_factory=list)
    linked_wallets: list[str] = field(default_factory=list)
    # Patterns
    sell_count: int = 0
    buy_count: int = 0
    large_sells: list[dict] = field(default_factory=list)


class WalletAnalyzer:
    """Analyze wallets to detect dev patterns, pump operators, and suspicious behavior."""

    def __init__(self, client: HeliusClient) -> None:
        self.client = client

    def analyze_wallet(self, address: str) -> WalletProfile:
        """Full wallet profiling."""
        profile = WalletProfile(address=address)

        # 1. SOL balance
        try:
            profile.sol_balance = self.client.get_balance(address)
        except Exception:
            pass

        # 2. Token holdings
        try:
            assets = self.client.get_assets_by_owner(address, limit=50)
            items = assets.get("items", [])
            profile.token_count = assets.get("total", len(items))
            profile.tokens = self._parse_token_holdings(items)
        except Exception:
            pass

        # 3. Recent transactions
        try:
            sigs = self.client.get_signatures(address, limit=30)
            profile.total_tx_count = len(sigs)
            profile.recent_transactions = self._parse_signatures(sigs)
        except Exception:
            pass

        # 4. Dev pattern detection
        self._detect_dev_patterns(profile)

        return profile

    def analyze_dev_wallet(self, dev_address: str, token_mint: str) -> dict:
        """Analyze a specific dev wallet in context of a token."""
        profile = self.analyze_wallet(dev_address)

        # Check if dev still holds the token
        dev_holds_token = False
        dev_token_amount = 0.0
        for t in profile.tokens:
            if t.get("mint") == token_mint:
                dev_holds_token = True
                dev_token_amount = t.get("amount", 0)
                break

        # Check recent sells of this token
        token_sells = []
        for tx in profile.recent_transactions:
            if tx.get("type") == "TRANSFER" and token_mint in str(tx):
                token_sells.append(tx)

        return {
            "wallet": dev_address,
            "sol_balance": profile.sol_balance,
            "total_tokens_held": profile.token_count,
            "holds_target_token": dev_holds_token,
            "target_token_amount": dev_token_amount,
            "recent_sells_of_token": len(token_sells),
            "is_suspected_serial_dev": profile.is_suspected_dev,
            "dev_signals": profile.dev_signals,
            "linked_wallets": profile.linked_wallets,
            "recommendation": self._dev_recommendation(profile, dev_holds_token),
        }

    def track_wallet_activity(self, address: str, limit: int = 20) -> list[dict]:
        """Get recent activity for a wallet with parsed details."""
        sigs = self.client.get_signatures(address, limit=limit)
        activities = []
        for sig_info in sigs:
            sig = sig_info.get("signature", "")
            try:
                tx = self.client.get_transaction(sig)
                if tx:
                    activities.append(self._parse_transaction_detail(tx, sig))
            except Exception:
                activities.append({
                    "signature": sig,
                    "status": sig_info.get("confirmationStatus", "unknown"),
                    "slot": sig_info.get("slot", 0),
                    "error": sig_info.get("err"),
                    "memo": sig_info.get("memo"),
                })
        return activities

    # ── Internal helpers ─────────────────────────────────────────────────

    def _parse_token_holdings(self, items: list) -> list[dict]:
        tokens = []
        for item in items:
            token_info = item.get("token_info", {})
            content = item.get("content", {})
            metadata = content.get("metadata", {})
            mint = item.get("id", "")

            amount = 0.0
            if token_info:
                decimals = token_info.get("decimals", 0)
                balance = float(token_info.get("balance", 0))
                amount = balance / (10 ** decimals) if decimals else balance

            tokens.append({
                "mint": mint,
                "name": metadata.get("name", "Inconnu"),
                "symbol": metadata.get("symbol", "???"),
                "amount": amount,
                "interface": item.get("interface", ""),
            })
        return tokens

    def _parse_signatures(self, sigs: list) -> list[dict]:
        return [
            {
                "signature": s.get("signature", ""),
                "slot": s.get("slot", 0),
                "block_time": s.get("blockTime"),
                "status": "success" if s.get("err") is None else "failed",
                "memo": s.get("memo"),
            }
            for s in sigs
        ]

    def _parse_transaction_detail(self, tx: dict, signature: str) -> dict:
        meta = tx.get("meta", {})
        block_time = tx.get("blockTime", 0)
        fee = meta.get("fee", 0) / 1e9 if meta else 0

        pre_balances = meta.get("preBalances", []) if meta else []
        post_balances = meta.get("postBalances", []) if meta else []

        sol_change = 0.0
        if pre_balances and post_balances:
            sol_change = (post_balances[0] - pre_balances[0]) / 1e9

        log_messages = meta.get("logMessages", []) if meta else []
        programs_used = set()
        for log in log_messages:
            if "invoke" in log.lower():
                parts = log.split()
                for p in parts:
                    if len(p) > 30:
                        programs_used.add(p)

        return {
            "signature": signature,
            "block_time": block_time,
            "fee_sol": fee,
            "sol_change": sol_change,
            "programs": list(programs_used)[:5],
            "status": "success" if meta and meta.get("err") is None else "failed",
        }

    def _detect_dev_patterns(self, profile: WalletProfile) -> None:
        """Heuristics to detect if a wallet belongs to a serial token deployer."""
        # Signal 1: Holds many low-value tokens
        low_value_tokens = sum(1 for t in profile.tokens if t.get("amount", 0) < 1)
        if low_value_tokens > 10:
            profile.dev_signals.append(
                f"Detient {low_value_tokens} tokens a solde quasi-nul (dump probable)"
            )
            profile.is_suspected_dev = True

        # Signal 2: High transaction frequency
        if profile.total_tx_count >= 30:
            profile.dev_signals.append(
                f"Activite de transaction elevee ({profile.total_tx_count} tx recentes)"
            )

        # Signal 3: Very high SOL balance (whale/dev fund)
        if profile.sol_balance > 100:
            profile.dev_signals.append(
                f"Balance SOL elevee: {profile.sol_balance:.2f} SOL"
            )

        # Signal 4: Many different token types
        if profile.token_count > 30:
            profile.dev_signals.append(
                f"Detient {profile.token_count} tokens differents (comportement de dev serial)"
            )
            profile.is_suspected_dev = True

    def _dev_recommendation(self, profile: WalletProfile, holds_token: bool) -> str:
        if profile.is_suspected_dev and not holds_token:
            return (
                "DANGER: Ce dev a probablement deja dump ses tokens. "
                "Wallet identifie comme dev serial. EVITER ce token."
            )
        if profile.is_suspected_dev and holds_token:
            return (
                "ATTENTION: Wallet identifie comme dev serial mais detient encore des tokens. "
                "Le dump peut arriver a tout moment. Surveillez de pres."
            )
        if not holds_token:
            return (
                "Le dev ne detient plus ce token. "
                "Verifiez s'il a vendu progressivement ou d'un coup (signe de dump)."
            )
        return "Le dev detient encore des tokens. Situation normale pour l'instant."
