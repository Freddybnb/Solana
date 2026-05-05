"""Core agent logic - natural language understanding and command routing."""

from __future__ import annotations

import re

from solana_agent import formatter
from solana_agent.bundle_detector import BundleDetector
from solana_agent.helius_client import HeliusClient
from solana_agent.rug_detector import RugDetector
from solana_agent.token_analyzer import TokenAnalyzer
from solana_agent.wallet_analyzer import WalletAnalyzer

# Solana address pattern (base58, 32-44 chars)
SOLANA_ADDR = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


class SolanaMemecoinAgent:
    """Expert IA en trading de memecoins sur Solana."""

    def __init__(self, api_key: str | None = None) -> None:
        self.client = HeliusClient(api_key=api_key)
        self.token_analyzer = TokenAnalyzer(self.client)
        self.rug_detector = RugDetector(self.client)
        self.wallet_analyzer = WalletAnalyzer(self.client)
        self.bundle_detector = BundleDetector(self.client)

    def close(self) -> None:
        self.client.close()

    def process_input(self, user_input: str) -> None:
        """Process user input - commands or natural language."""
        text = user_input.strip()
        if not text:
            return

        lower = text.lower()

        # Direct commands
        if lower.startswith("analyse ") or lower.startswith("analyze "):
            addr = self._extract_address(text)
            if addr:
                self._cmd_analyze_token(addr)
            else:
                formatter.console.print("[red]Adresse invalide. Usage: analyse <mint_address>[/red]")
            return

        if lower.startswith("risque ") or lower.startswith("risk ") or lower.startswith("rug "):
            addr = self._extract_address(text)
            if addr:
                self._cmd_risk_analysis(addr)
            else:
                formatter.console.print("[red]Adresse invalide. Usage: risque <mint_address>[/red]")
            return

        if lower.startswith("holders "):
            addr = self._extract_address(text)
            if addr:
                self._cmd_holders(addr)
            else:
                formatter.console.print("[red]Adresse invalide. Usage: holders <mint_address>[/red]")
            return

        if lower.startswith("wallet "):
            addr = self._extract_address(text)
            if addr:
                self._cmd_wallet(addr)
            else:
                formatter.console.print("[red]Adresse invalide. Usage: wallet <adresse>[/red]")
            return

        if lower.startswith("dev "):
            addrs = SOLANA_ADDR.findall(text)
            if len(addrs) >= 2:
                self._cmd_dev_analysis(addrs[0], addrs[1])
            elif len(addrs) == 1:
                formatter.console.print(
                    "[yellow]Usage: dev <adresse_dev> <mint_token>[/yellow]\n"
                    "Vous n'avez fourni qu'une adresse. J'analyse le wallet..."
                )
                self._cmd_wallet(addrs[0])
            else:
                formatter.console.print("[red]Usage: dev <adresse_dev> <mint_token>[/red]")
            return

        if lower.startswith("bundle ") or lower.startswith("bundled "):
            addr = self._extract_address(text)
            if addr:
                self._cmd_bundle_analysis(addr)
            else:
                formatter.console.print("[red]Adresse invalide. Usage: bundle <mint_address>[/red]")
            return

        if lower.startswith("tx ") or lower.startswith("transactions "):
            addr = self._extract_address(text)
            if addr:
                self._cmd_transactions(addr)
            else:
                formatter.console.print("[red]Adresse invalide. Usage: tx <adresse>[/red]")
            return

        if lower.startswith("balance "):
            addr = self._extract_address(text)
            if addr:
                self._cmd_balance(addr)
            else:
                formatter.console.print("[red]Adresse invalide. Usage: balance <adresse>[/red]")
            return

        if lower in ("aide", "help", "?"):
            formatter.print_help()
            return

        if lower in ("quitter", "quit", "exit", "q"):
            raise SystemExit(0)

        # Natural language fallback
        self._handle_natural_language(text)

    # ── Commands ─────────────────────────────────────────────────────────

    def _cmd_analyze_token(self, mint: str) -> None:
        formatter.console.print(f"[dim]Analyse du token {mint[:16]}...[/dim]")
        info = self.token_analyzer.analyze_token(mint)
        formatter.print_token_info(info)

    def _cmd_risk_analysis(self, mint: str) -> None:
        formatter.console.print(f"[dim]Analyse de risque en cours pour {mint[:16]}...[/dim]")
        report = self.rug_detector.full_risk_analysis(mint)
        formatter.print_risk_report(report)

    def _cmd_holders(self, mint: str) -> None:
        formatter.console.print(f"[dim]Recuperation des holders pour {mint[:16]}...[/dim]")
        data = self.token_analyzer.get_holder_distribution(mint)
        formatter.print_holder_distribution(data)

    def _cmd_wallet(self, address: str) -> None:
        formatter.console.print(f"[dim]Analyse du wallet {address[:16]}...[/dim]")
        profile = self.wallet_analyzer.analyze_wallet(address)
        formatter.print_wallet_profile(profile)

    def _cmd_bundle_analysis(self, mint: str) -> None:
        formatter.console.print(f"[dim]Detection de bundle pour {mint[:16]}... (analyse des premieres transactions)[/dim]")
        # Get token name first
        token_name = ""
        try:
            info = self.token_analyzer.analyze_token(mint)
            token_name = f"{info.name} ({info.symbol})"
        except Exception:
            pass
        report = self.bundle_detector.analyze_bundle(mint, token_name=token_name)
        formatter.print_bundle_report(report)

    def _cmd_dev_analysis(self, dev_addr: str, mint: str) -> None:
        formatter.console.print(
            f"[dim]Analyse du dev {dev_addr[:16]}... pour le token {mint[:16]}...[/dim]"
        )
        data = self.wallet_analyzer.analyze_dev_wallet(dev_addr, mint)
        formatter.print_dev_analysis(data)

    def _cmd_transactions(self, address: str) -> None:
        formatter.console.print(f"[dim]Recuperation des transactions pour {address[:16]}...[/dim]")
        activities = self.wallet_analyzer.track_wallet_activity(address, limit=10)

        from rich.table import Table

        table = Table(title="Transactions recentes", border_style="cyan")
        table.add_column("Signature", width=20)
        table.add_column("Status", width=10)
        table.add_column("SOL Change", width=15)
        table.add_column("Fee", width=10)
        table.add_column("Programmes", width=30)

        for tx in activities:
            sig = tx.get("signature", "")[:20] + "..."
            status = tx.get("status", "?")
            sol_change = tx.get("sol_change", 0)
            fee = tx.get("fee_sol", 0)
            programs = ", ".join(tx.get("programs", []))[:30]

            status_style = "green" if status == "success" else "red"
            change_style = "green" if sol_change > 0 else "red" if sol_change < 0 else "white"

            table.add_row(
                sig,
                f"[{status_style}]{status}[/{status_style}]",
                f"[{change_style}]{sol_change:+.6f}[/{change_style}]",
                f"{fee:.6f}",
                programs,
            )
        formatter.console.print(table)

    def _cmd_balance(self, address: str) -> None:
        balance = self.client.get_balance(address)
        formatter.console.print(f"[bold]Balance:[/bold] {balance:.9f} SOL")

    # ── Natural language processing ──────────────────────────────────────

    def _handle_natural_language(self, text: str) -> None:
        """Parse natural language queries and route to appropriate commands."""
        lower = text.lower()
        addr = self._extract_address(text)

        # Rug pull / risk questions
        rug_keywords = [
            "rug", "scam", "arnaque", "risque", "risk", "danger",
            "safe", "sur", "fiable", "confiance", "legit",
            "honeypot", "honey pot", "pump", "dump",
        ]
        if any(kw in lower for kw in rug_keywords) and addr:
            self._cmd_risk_analysis(addr)
            return

        # Token analysis
        token_keywords = [
            "token", "analyse", "analyze", "info", "information",
            "metadata", "supply", "detail", "c'est quoi", "qu'est",
        ]
        if any(kw in lower for kw in token_keywords) and addr:
            self._cmd_analyze_token(addr)
            return

        # Holder questions
        holder_keywords = ["holder", "holders", "detenteur", "distribution", "qui detient", "whale"]
        if any(kw in lower for kw in holder_keywords) and addr:
            self._cmd_holders(addr)
            return

        # Wallet questions
        wallet_keywords = ["wallet", "portefeuille", "adresse", "compte"]
        if any(kw in lower for kw in wallet_keywords) and addr:
            self._cmd_wallet(addr)
            return

        # Bundle detection
        bundle_keywords = ["bundle", "bundled", "coordonne", "coordinated", "snipe", "sniped"]
        if any(kw in lower for kw in bundle_keywords) and addr:
            self._cmd_bundle_analysis(addr)
            return

        # Dev questions
        dev_keywords = ["dev", "developpeur", "createur", "creator", "deployer"]
        if any(kw in lower for kw in dev_keywords) and addr:
            self._cmd_wallet(addr)
            return

        # Transaction questions
        tx_keywords = ["transaction", "tx", "historique", "activite"]
        if any(kw in lower for kw in tx_keywords) and addr:
            self._cmd_transactions(addr)
            return

        # Balance questions
        balance_keywords = ["balance", "solde", "combien de sol"]
        if any(kw in lower for kw in balance_keywords) and addr:
            self._cmd_balance(addr)
            return

        # If we have an address but no clear intent, do a risk analysis
        if addr:
            formatter.console.print(
                "[yellow]J'ai detecte une adresse Solana. "
                "Je lance une analyse de risque par defaut...[/yellow]"
            )
            self._cmd_risk_analysis(addr)
            return

        # General memecoin knowledge
        self._memecoin_knowledge(lower)

    def _memecoin_knowledge(self, text: str) -> None:
        """Respond with memecoin trading knowledge."""
        knowledge = {
            "rug pull": (
                "[bold red]Rug Pull[/bold red] - Le dev retire toute la liquidite du pool.\n"
                "Signes: mint authority actif, freeze authority, concentration des holders elevee, "
                "liquidite non verrouillee, pas de reseaux sociaux verifies.\n"
                "Protection: Verifiez toujours avec la commande [cyan]risque <mint>[/cyan]"
            ),
            "honeypot": (
                "[bold red]Honeypot[/bold red] - Vous pouvez acheter mais pas vendre.\n"
                "Le freeze authority permet au dev de bloquer vos tokens.\n"
                "Verifiez avec [cyan]risque <mint>[/cyan] - le flag FREEZE_AUTHORITY sera detecte."
            ),
            "pump and dump": (
                "[bold red]Pump & Dump[/bold red] - Le dev ou un groupe coordonne achete massivement "
                "pour faire monter le prix, puis vend tout d'un coup.\n"
                "Signes: Volume soudain, concentration des wallets, dev qui detient beaucoup.\n"
                "Verifiez: [cyan]holders <mint>[/cyan] et [cyan]dev <wallet> <mint>[/cyan]"
            ),
            "slow rug": (
                "[bold yellow]Slow Rug[/bold yellow] - Le dev vend progressivement ses tokens "
                "sur plusieurs jours/semaines pour ne pas eveiller les soupcons.\n"
                "Detection: Surveillez les transactions du dev avec [cyan]tx <dev_wallet>[/cyan]"
            ),
            "mint authority": (
                "[bold]Mint Authority[/bold] - Permet de creer de nouveaux tokens.\n"
                "Si actif: le dev peut diluer la supply a tout moment = DANGER.\n"
                "Un token sur devrait avoir le mint authority desactive (renounced)."
            ),
            "freeze authority": (
                "[bold]Freeze Authority[/bold] - Permet de geler les comptes de tokens.\n"
                "Si actif: le dev peut vous empecher de vendre = HONEYPOT potentiel.\n"
                "Un token sur devrait avoir le freeze authority desactive."
            ),
            "bundled": (
                "[bold yellow]Bundled Launch[/bold yellow] - Le dev achete avec plusieurs wallets "
                "dans la meme transaction de creation du token.\n"
                "Permet de cacher la concentration reelle des holdings.\n"
                "Detectez avec [cyan]holders <mint>[/cyan] et cherchez des wallets lies."
            ),
            "cto": (
                "[bold green]CTO (Community Takeover)[/bold green] - La communaute reprend un token "
                "abandonne par le dev original.\n"
                "Peut etre positif si la communaute est solide, mais mefiez-vous des faux CTOs."
            ),
        }

        for keyword, response in knowledge.items():
            if keyword in text:
                formatter.console.print(response)
                return

        # Default help
        formatter.console.print(
            "[yellow]Je suis votre expert en memecoins Solana. "
            "Voici ce que je peux faire:[/yellow]"
        )
        formatter.print_help()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _extract_address(self, text: str) -> str | None:
        match = SOLANA_ADDR.search(text)
        return match.group(0) if match else None
