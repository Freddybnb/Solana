"""Bundled launch detection - detect coordinated buys at token creation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from solana_agent.helius_client import HeliusClient


@dataclass
class BundleWallet:
    address: str = ""
    bought_in_slot: int = 0
    sol_spent: float = 0.0
    token_amount: float = 0.0
    funded_by: str = ""
    tx_signature: str = ""


@dataclass
class BundleReport:
    mint: str = ""
    token_name: str = ""
    is_bundled: bool = False
    bundle_score: int = 0  # 0-100
    total_early_buyers: int = 0
    same_block_buyers: int = 0
    same_slot_groups: dict[int, list[BundleWallet]] = field(default_factory=dict)
    linked_wallets: list[list[str]] = field(default_factory=list)
    funding_sources: dict[str, list[str]] = field(default_factory=dict)
    early_wallets: list[BundleWallet] = field(default_factory=list)
    first_slot: int = 0
    creation_tx: str = ""
    summary: str = ""
    risk_level: str = ""


class BundleDetector:
    """Detect bundled launches where devs buy with multiple wallets at creation."""

    EARLY_SLOTS_WINDOW = 5

    def __init__(self, client: HeliusClient) -> None:
        self.client = client

    def analyze_bundle(self, mint: str, token_name: str = "") -> BundleReport:
        """Full bundled launch analysis for a token."""
        report = BundleReport(mint=mint, token_name=token_name)

        # 1. Get earliest transactions for this token
        early_txs = self._get_early_transactions(mint)
        if not early_txs:
            report.summary = "Impossible de recuperer les transactions initiales du token."
            report.risk_level = "INCONNU"
            return report

        # 2. Parse early buyers
        early_wallets = self._parse_early_buyers(early_txs, mint)
        report.early_wallets = early_wallets
        report.total_early_buyers = len(early_wallets)

        if not early_wallets:
            report.summary = "Aucun acheteur initial detecte dans les premieres transactions."
            report.risk_level = "INCONNU"
            return report

        # 3. Find the creation slot
        report.first_slot = min(w.bought_in_slot for w in early_wallets)
        report.creation_tx = early_wallets[0].tx_signature if early_wallets else ""

        # 4. Group by slot (same block = highly suspicious)
        slot_groups: dict[int, list[BundleWallet]] = defaultdict(list)
        for w in early_wallets:
            slot_groups[w.bought_in_slot].append(w)
        report.same_slot_groups = dict(slot_groups)

        # Count wallets in same block as creation
        creation_block_wallets = 0
        for slot, wallets in slot_groups.items():
            if slot <= report.first_slot + self.EARLY_SLOTS_WINDOW:
                creation_block_wallets += len(wallets)
        report.same_block_buyers = creation_block_wallets

        # 5. Detect linked wallets via funding source
        report.funding_sources = self._trace_funding_sources(early_wallets)
        report.linked_wallets = self._find_linked_groups(report.funding_sources)

        # 6. Calculate bundle score
        report.bundle_score = self._calculate_score(report)
        report.is_bundled = report.bundle_score >= 40
        report.risk_level = self._score_to_level(report.bundle_score)
        report.summary = self._generate_summary(report)

        return report

    def _get_early_transactions(self, mint: str) -> list[dict]:
        """Get the earliest transactions involving this token mint."""
        try:
            sigs = self.client.get_signatures(mint, limit=20)
            if not sigs:
                return []

            # Sort by slot ascending to get earliest first
            sigs.sort(key=lambda s: s.get("slot", 0))

            # Parse the earliest transactions
            parsed_txs = []
            for sig_info in sigs[:15]:
                sig = sig_info.get("signature", "")
                try:
                    tx = self.client.get_transaction(sig)
                    if tx:
                        tx["_slot"] = sig_info.get("slot", 0)
                        tx["_signature"] = sig
                        parsed_txs.append(tx)
                except Exception:
                    continue
            return parsed_txs
        except Exception:
            return []

    def _parse_early_buyers(self, txs: list[dict], mint: str) -> list[BundleWallet]:
        """Extract buyer wallets from early transactions."""
        wallets: list[BundleWallet] = []
        seen_addresses: set[str] = set()

        for tx in txs:
            slot = tx.get("_slot", 0)
            signature = tx.get("_signature", "")
            meta = tx.get("meta")
            if not meta:
                continue

            transaction = tx.get("transaction", {})
            message = transaction.get("message", {})
            account_keys = message.get("accountKeys", [])

            # Get pre/post token balances to find who received tokens
            pre_token = meta.get("preTokenBalances", [])
            post_token = meta.get("postTokenBalances", [])

            # Build map of post-token balances for this mint
            post_balances: dict[int, dict] = {}
            for bal in post_token:
                if bal.get("mint") == mint:
                    idx = bal.get("accountIndex", -1)
                    post_balances[idx] = bal

            pre_balances: dict[int, dict] = {}
            for bal in pre_token:
                if bal.get("mint") == mint:
                    idx = bal.get("accountIndex", -1)
                    pre_balances[idx] = bal

            # Find accounts that gained tokens (buyers)
            for idx, post_bal in post_balances.items():
                post_amount = float(post_bal.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
                pre_bal = pre_balances.get(idx, {})
                pre_amount = float(pre_bal.get("uiTokenAmount", {}).get("uiAmount", 0) or 0) if pre_bal else 0.0

                gained = post_amount - pre_amount
                if gained <= 0:
                    continue

                owner = post_bal.get("owner", "")
                if not owner or owner in seen_addresses:
                    continue

                seen_addresses.add(owner)

                # Calculate SOL spent (from pre/post SOL balances)
                sol_spent = 0.0
                pre_sol = meta.get("preBalances", [])
                post_sol = meta.get("postBalances", [])
                # Find the account index for this owner
                for acct_idx, acct in enumerate(account_keys):
                    acct_key = acct if isinstance(acct, str) else acct.get("pubkey", "")
                    if acct_key == owner:
                        if acct_idx < len(pre_sol) and acct_idx < len(post_sol):
                            sol_spent = (pre_sol[acct_idx] - post_sol[acct_idx]) / 1e9
                        break

                wallets.append(BundleWallet(
                    address=owner,
                    bought_in_slot=slot,
                    sol_spent=max(0, sol_spent),
                    token_amount=gained,
                    tx_signature=signature,
                ))

        return wallets

    def _trace_funding_sources(self, wallets: list[BundleWallet]) -> dict[str, list[str]]:
        """Trace where early buyer wallets got their SOL from."""
        funding: dict[str, list[str]] = defaultdict(list)

        for wallet in wallets[:10]:  # Limit API calls
            try:
                sigs = self.client.get_signatures(wallet.address, limit=5)
                # Sort oldest first
                sigs.sort(key=lambda s: s.get("slot", 0))

                for sig_info in sigs[:3]:
                    sig = sig_info.get("signature", "")
                    try:
                        tx = self.client.get_transaction(sig)
                        if not tx:
                            continue
                        meta = tx.get("meta", {})
                        transaction = tx.get("transaction", {})
                        message = transaction.get("message", {})
                        account_keys = message.get("accountKeys", [])

                        post_sol = meta.get("postBalances", [])
                        pre_sol = meta.get("preBalances", [])

                        # Find who sent SOL to this wallet
                        for acct_idx, acct in enumerate(account_keys):
                            acct_key = acct if isinstance(acct, str) else acct.get("pubkey", "")
                            if acct_idx < len(pre_sol) and acct_idx < len(post_sol):
                                sol_delta = (pre_sol[acct_idx] - post_sol[acct_idx]) / 1e9
                                if sol_delta > 0.01 and acct_key != wallet.address:
                                    wallet.funded_by = acct_key
                                    funding[acct_key].append(wallet.address)
                                    break
                    except Exception:
                        continue
            except Exception:
                continue

        return dict(funding)

    def _find_linked_groups(self, funding: dict[str, list[str]]) -> list[list[str]]:
        """Find groups of wallets funded by the same source."""
        groups = []
        for source, funded_wallets in funding.items():
            if len(funded_wallets) >= 2:
                groups.append([source] + funded_wallets)
        return groups

    def _calculate_score(self, report: BundleReport) -> int:
        """Calculate bundle risk score (0-100)."""
        score = 0

        # Multiple buyers in same block as creation (strongest signal)
        if report.same_block_buyers >= 5:
            score += 40
        elif report.same_block_buyers >= 3:
            score += 30
        elif report.same_block_buyers >= 2:
            score += 20

        # Linked wallets (funded by same source)
        if report.linked_wallets:
            largest_group = max(len(g) for g in report.linked_wallets)
            if largest_group >= 5:
                score += 35
            elif largest_group >= 3:
                score += 25
            elif largest_group >= 2:
                score += 15

        # Many early buyers in narrow window
        if report.total_early_buyers >= 10:
            score += 15
        elif report.total_early_buyers >= 5:
            score += 10

        # Same slot concentration
        max_in_slot = max((len(ws) for ws in report.same_slot_groups.values()), default=0)
        if max_in_slot >= 4:
            score += 10

        return min(100, score)

    def _score_to_level(self, score: int) -> str:
        if score >= 70:
            return "CRITIQUE"
        if score >= 50:
            return "ELEVE"
        if score >= 30:
            return "MOYEN"
        if score >= 15:
            return "FAIBLE"
        return "SAFE"

    def _generate_summary(self, report: BundleReport) -> str:
        if report.bundle_score >= 70:
            return (
                f"ALERTE BUNDLE - {report.same_block_buyers} wallets ont achete dans les premiers blocs. "
                f"{len(report.linked_wallets)} groupe(s) de wallets lies detecte(s). "
                "Tres forte probabilite de launch bundle par le dev. Le dev controle probablement "
                "une grande partie de la supply via des wallets multiples."
            )
        if report.bundle_score >= 50:
            return (
                f"SUSPICION DE BUNDLE - {report.same_block_buyers} acheteurs initiaux detectes. "
                f"{len(report.linked_wallets)} groupe(s) potentiellement lies. "
                "Plusieurs indicateurs suggerent un lancement coordonne."
            )
        if report.bundle_score >= 30:
            return (
                f"ACTIVITE SUSPECTE - {report.total_early_buyers} acheteurs dans les premieres transactions. "
                "Quelques signes de coordination possibles."
            )
        if report.bundle_score >= 15:
            return (
                f"RISQUE FAIBLE - {report.total_early_buyers} acheteurs initiaux. "
                "Peu de signes de bundling detectes."
            )
        return (
            f"PAS DE BUNDLE DETECTE - {report.total_early_buyers} acheteurs initiaux analyses. "
            "Le lancement semble organique."
        )
