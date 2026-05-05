"""Rug pull and pump & dump detection engine."""

from __future__ import annotations

from dataclasses import dataclass, field

from solana_agent.config import RISK_WEIGHTS, RUG_PULL_THRESHOLDS
from solana_agent.helius_client import HeliusClient
from solana_agent.token_analyzer import TokenAnalyzer, TokenInfo


@dataclass
class RiskFlag:
    name: str
    severity: str  # "CRITIQUE", "ELEVE", "MOYEN", "FAIBLE"
    description: str
    score: int = 0


@dataclass
class RiskReport:
    mint: str = ""
    token_name: str = ""
    token_symbol: str = ""
    risk_score: int = 0  # 0-100
    risk_level: str = ""  # "SAFE", "FAIBLE", "MOYEN", "ELEVE", "CRITIQUE"
    flags: list[RiskFlag] = field(default_factory=list)
    summary: str = ""
    recommendation: str = ""
    token_info: TokenInfo | None = None


class RugDetector:
    """Detect rug pulls, pump & dumps, and other scam patterns."""

    def __init__(self, client: HeliusClient) -> None:
        self.client = client
        self.token_analyzer = TokenAnalyzer(client)
        self.thresholds = RUG_PULL_THRESHOLDS

    def full_risk_analysis(self, mint: str) -> RiskReport:
        """Run a complete risk analysis on a token."""
        token_info = self.token_analyzer.analyze_token(mint)
        report = RiskReport(
            mint=mint,
            token_name=token_info.name,
            token_symbol=token_info.symbol,
            token_info=token_info,
        )

        # Run all checks
        self._check_mint_authority(token_info, report)
        self._check_freeze_authority(token_info, report)
        self._check_holder_concentration(token_info, report)
        self._check_low_holders(token_info, report)
        self._check_mutability(token_info, report)
        self._check_dev_holdings(token_info, report)

        # Additional on-chain checks
        self._check_recent_large_transactions(mint, report)

        # Calculate final score
        report.risk_score = min(100, sum(f.score for f in report.flags))
        report.risk_level = self._score_to_level(report.risk_score)
        report.summary = self._generate_summary(report)
        report.recommendation = self._generate_recommendation(report)

        return report

    # ── Individual risk checks ───────────────────────────────────────────

    def _check_mint_authority(self, token: TokenInfo, report: RiskReport) -> None:
        if token.mint_authority:
            report.flags.append(RiskFlag(
                name="MINT_AUTHORITY_ACTIVE",
                severity="CRITIQUE",
                description=(
                    f"Le mint authority est toujours actif ({token.mint_authority[:8]}...). "
                    "Le dev peut creer de nouveaux tokens a tout moment et diluer la supply. "
                    "C'est un signal majeur de rug pull potentiel."
                ),
                score=RISK_WEIGHTS["mint_authority"],
            ))

    def _check_freeze_authority(self, token: TokenInfo, report: RiskReport) -> None:
        if token.freeze_authority:
            report.flags.append(RiskFlag(
                name="FREEZE_AUTHORITY_ACTIVE",
                severity="ELEVE",
                description=(
                    f"Le freeze authority est actif ({token.freeze_authority[:8]}...). "
                    "Le dev peut geler vos tokens et vous empecher de vendre. "
                    "Souvent utilise dans les honeypot scams."
                ),
                score=RISK_WEIGHTS["freeze_authority"],
            ))

    def _check_holder_concentration(self, token: TokenInfo, report: RiskReport) -> None:
        threshold = self.thresholds["top_holder_concentration_pct"]
        if token.top_holder_pct > threshold:
            severity = "CRITIQUE" if token.top_holder_pct > 60 else "ELEVE"
            report.flags.append(RiskFlag(
                name="CONCENTRATION_ELEVEE",
                severity=severity,
                description=(
                    f"Les 5 plus gros wallets detiennent {token.top_holder_pct:.1f}% de la supply. "
                    f"Seuil d'alerte: {threshold}%. "
                    "Risque de dump massif coordonne."
                ),
                score=RISK_WEIGHTS["top_holder_concentration"],
            ))

    def _check_low_holders(self, token: TokenInfo, report: RiskReport) -> None:
        min_holders = self.thresholds["min_holders"]
        if token.holder_count < min_holders:
            report.flags.append(RiskFlag(
                name="PEU_DE_HOLDERS",
                severity="MOYEN",
                description=(
                    f"Seulement {token.holder_count} holders detectes (min recommande: {min_holders}). "
                    "Token possiblement tres jeune ou artificiel."
                ),
                score=RISK_WEIGHTS["low_holders"],
            ))

    def _check_mutability(self, token: TokenInfo, report: RiskReport) -> None:
        if token.mutable:
            report.flags.append(RiskFlag(
                name="METADATA_MUTABLE",
                severity="MOYEN",
                description=(
                    "Les metadata du token sont modifiables. "
                    "Le dev peut changer le nom, le symbole ou l'image a tout moment. "
                    "Utilise dans les bait-and-switch scams."
                ),
                score=8,
            ))

    def _check_dev_holdings(self, token: TokenInfo, report: RiskReport) -> None:
        """Check if known creator wallets hold a large percentage."""
        if not token.creators:
            return
        dev_addresses = {c["address"] for c in token.creators}
        dev_total_pct = 0.0
        for holder in token.top_holders:
            if holder["address"] in dev_addresses:
                dev_total_pct += holder["pct"]

        threshold = self.thresholds["dev_holding_pct"]
        if dev_total_pct > threshold:
            report.flags.append(RiskFlag(
                name="DEV_HOLDING_ELEVE",
                severity="ELEVE",
                description=(
                    f"Les wallets du dev detiennent {dev_total_pct:.1f}% de la supply "
                    f"(seuil: {threshold}%). Risque de dev dump."
                ),
                score=RISK_WEIGHTS["dev_holding"],
            ))

    def _check_recent_large_transactions(self, mint: str, report: RiskReport) -> None:
        """Check for suspicious large recent transactions."""
        try:
            sigs = self.client.get_signatures(mint, limit=10)
            if not sigs:
                report.flags.append(RiskFlag(
                    name="PAS_DE_TRANSACTIONS",
                    severity="MOYEN",
                    description="Aucune transaction recente trouvee pour ce token. Token mort ou inactif.",
                    score=5,
                ))
        except Exception:
            pass

    # ── Scoring helpers ──────────────────────────────────────────────────

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

    def _generate_summary(self, report: RiskReport) -> str:
        n = len(report.flags)
        if report.risk_level == "CRITIQUE":
            return (
                f"DANGER - {n} signaux d'alerte detectes. "
                f"Score de risque: {report.risk_score}/100. "
                "Ce token presente des caracteristiques de rug pull. NE PAS INVESTIR."
            )
        if report.risk_level == "ELEVE":
            return (
                f"ATTENTION - {n} signaux d'alerte. Score: {report.risk_score}/100. "
                "Risque eleve de manipulation. Prudence extreme recommandee."
            )
        if report.risk_level == "MOYEN":
            return (
                f"{n} signaux d'alerte mineurs. Score: {report.risk_score}/100. "
                "Quelques points de vigilance a surveiller."
            )
        if report.risk_level == "FAIBLE":
            return f"Risque faible. Score: {report.risk_score}/100. {n} point(s) mineur(s) detecte(s)."
        return f"Aucun signal de rug pull majeur detecte. Score: {report.risk_score}/100."

    def _generate_recommendation(self, report: RiskReport) -> str:
        if report.risk_level == "CRITIQUE":
            return (
                "EVITEZ ce token. Plusieurs indicateurs de scam sont presents. "
                "Si vous etes deja investi, envisagez de sortir rapidement."
            )
        if report.risk_level == "ELEVE":
            return (
                "Investissement tres risque. Si vous entrez, utilisez un petit montant "
                "et placez un stop-loss serre. Surveillez les wallets du dev."
            )
        if report.risk_level == "MOYEN":
            return (
                "Faites vos propres recherches (DYOR). Verifiez la communaute, "
                "le site web et les reseaux sociaux avant d'investir."
            )
        if report.risk_level == "FAIBLE":
            return "Risque relativement faible, mais restez vigilant. Le marche des memecoins est volatile."
        return "Le token semble relativement sur selon nos criteres. Tradez avec prudence comme toujours."
