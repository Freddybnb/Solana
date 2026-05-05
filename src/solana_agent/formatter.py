"""Rich formatting for the agent output."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from solana_agent.rug_detector import RiskReport
from solana_agent.token_analyzer import TokenInfo
from solana_agent.wallet_analyzer import WalletProfile

console = Console()


def risk_color(level: str) -> str:
    colors = {
        "CRITIQUE": "bold red",
        "ELEVE": "red",
        "MOYEN": "yellow",
        "FAIBLE": "green",
        "SAFE": "bold green",
    }
    return colors.get(level, "white")


def print_banner() -> None:
    banner = Text()
    banner.append("  SOLANA MEMECOIN AGENT  ", style="bold white on blue")
    banner.append("\n  Expert IA en trading de memecoins  ", style="dim")
    console.print(Panel(banner, border_style="blue", padding=(1, 2)))


def print_token_info(info: TokenInfo) -> None:
    table = Table(title=f"Token: {info.name} ({info.symbol})", border_style="cyan")
    table.add_column("Propriete", style="bold cyan", width=25)
    table.add_column("Valeur", style="white")

    table.add_row("Mint", info.mint)
    table.add_row("Nom", info.name)
    table.add_row("Symbole", info.symbol)
    table.add_row("Decimales", str(info.decimals))
    table.add_row("Supply", f"{info.supply:,.2f}" if info.supply else "N/A")
    table.add_row(
        "Mint Authority",
        Text(info.mint_authority or "Desactive", style="red" if info.mint_authority else "green"),
    )
    table.add_row(
        "Freeze Authority",
        Text(info.freeze_authority or "Desactive", style="red" if info.freeze_authority else "green"),
    )
    table.add_row(
        "Update Authority",
        info.update_authority or "N/A",
    )
    table.add_row("Mutable", Text("Oui", style="yellow") if info.mutable else Text("Non", style="green"))
    table.add_row("Holders (top 20)", str(info.holder_count))
    table.add_row("Top 5 holders %", f"{info.top_holder_pct:.2f}%")

    if info.creators:
        creators_str = ", ".join(c["address"][:12] + "..." for c in info.creators[:3])
        table.add_row("Createurs", creators_str)

    if info.description:
        table.add_row("Description", info.description[:200])

    console.print(table)


def print_risk_report(report: RiskReport) -> None:
    color = risk_color(report.risk_level)

    # Header
    header = Text()
    header.append(f"  ANALYSE DE RISQUE - {report.token_name} ({report.token_symbol})  \n", style="bold")
    header.append(f"  Score: {report.risk_score}/100  ", style=color)
    header.append(f"  Niveau: {report.risk_level}  ", style=color)
    console.print(Panel(header, border_style=color.split()[-1] if " " in color else color))

    # Flags table
    if report.flags:
        table = Table(title="Signaux d'alerte", border_style="red")
        table.add_column("Signal", style="bold", width=25)
        table.add_column("Severite", width=10)
        table.add_column("Description", width=60)
        table.add_column("Score", width=6)

        for flag in report.flags:
            sev_color = risk_color(flag.severity)
            table.add_row(
                flag.name,
                Text(flag.severity, style=sev_color),
                flag.description,
                str(flag.score),
            )
        console.print(table)
    else:
        console.print("[green]Aucun signal d'alerte detecte.[/green]")

    # Summary
    console.print(Panel(report.summary, title="Resume", border_style="yellow"))
    console.print(Panel(report.recommendation, title="Recommandation", border_style="blue"))


def print_wallet_profile(profile: WalletProfile) -> None:
    table = Table(title=f"Profil Wallet: {profile.address[:16]}...", border_style="magenta")
    table.add_column("Propriete", style="bold magenta", width=25)
    table.add_column("Valeur", style="white")

    table.add_row("Adresse", profile.address)
    table.add_row("Balance SOL", f"{profile.sol_balance:.4f} SOL")
    table.add_row("Nombre de tokens", str(profile.token_count))
    table.add_row("Transactions recentes", str(profile.total_tx_count))
    table.add_row(
        "Dev suspect",
        Text("OUI", style="bold red") if profile.is_suspected_dev else Text("Non", style="green"),
    )

    if profile.dev_signals:
        signals = "\n".join(f"- {s}" for s in profile.dev_signals)
        table.add_row("Signaux dev", signals)

    console.print(table)

    # Token holdings
    if profile.tokens:
        tok_table = Table(title="Holdings", border_style="cyan")
        tok_table.add_column("Token", width=20)
        tok_table.add_column("Symbole", width=10)
        tok_table.add_column("Montant", width=20)
        tok_table.add_column("Mint", width=20)

        for t in profile.tokens[:15]:
            tok_table.add_row(
                t.get("name", "?")[:20],
                t.get("symbol", "?"),
                f"{t.get('amount', 0):,.4f}",
                t.get("mint", "")[:20] + "...",
            )
        console.print(tok_table)


def print_holder_distribution(data: dict) -> None:
    risk_text = data.get("concentration_risk", "INCONNU")
    color = {"ELEVE": "red", "MOYEN": "yellow", "FAIBLE": "green"}.get(risk_text, "white")

    console.print(Panel(
        f"Risque de concentration: [{color}]{risk_text}[/{color}]",
        title="Distribution des holders",
        border_style=color,
    ))

    table = Table(border_style="cyan")
    table.add_column("Metrique", style="bold cyan")
    table.add_column("Valeur")

    table.add_row("Top 1 holder", f"{data['top_1_pct']}%")
    table.add_row("Top 5 holders", f"{data['top_5_pct']}%")
    table.add_row("Top 10 holders", f"{data['top_10_pct']}%")
    table.add_row("Top 20 holders", f"{data['top_20_pct']}%")
    console.print(table)

    # Individual holders
    holders = data.get("holders", [])[:10]
    if holders:
        h_table = Table(title="Top 10 Holders", border_style="yellow")
        h_table.add_column("#", width=4)
        h_table.add_column("Adresse", width=48)
        h_table.add_column("%", width=10)
        h_table.add_column("Montant", width=20)

        for i, h in enumerate(holders, 1):
            h_table.add_row(
                str(i),
                h["address"],
                f"{h['pct']:.2f}%",
                f"{h['amount']:,.2f}",
            )
        console.print(h_table)


def print_dev_analysis(data: dict) -> None:
    color = "red" if data.get("is_suspected_serial_dev") else "green"
    table = Table(title="Analyse Dev Wallet", border_style=color)
    table.add_column("Propriete", style="bold", width=25)
    table.add_column("Valeur")

    table.add_row("Wallet", data.get("wallet", ""))
    table.add_row("Balance SOL", f"{data.get('sol_balance', 0):.4f} SOL")
    table.add_row("Tokens detenus", str(data.get("total_tokens_held", 0)))
    table.add_row(
        "Detient le token cible",
        Text("Oui", style="green") if data.get("holds_target_token") else Text("Non", style="red"),
    )
    table.add_row("Montant token cible", f"{data.get('target_token_amount', 0):,.2f}")
    table.add_row(
        "Dev serial suspect",
        Text("OUI", style="bold red") if data.get("is_suspected_serial_dev") else Text("Non", style="green"),
    )

    if data.get("dev_signals"):
        signals = "\n".join(f"- {s}" for s in data["dev_signals"])
        table.add_row("Signaux", signals)

    console.print(table)
    console.print(Panel(data.get("recommendation", ""), title="Recommandation", border_style="yellow"))


def print_help() -> None:
    help_table = Table(title="Commandes disponibles", border_style="blue")
    help_table.add_column("Commande", style="bold cyan", width=40)
    help_table.add_column("Description", style="white")

    commands = [
        ("analyse <mint>", "Analyse complete d'un token (metadata, supply, holders)"),
        ("risque <mint>", "Analyse de risque / detection rug pull"),
        ("holders <mint>", "Distribution des holders d'un token"),
        ("wallet <adresse>", "Profil complet d'un wallet"),
        ("dev <adresse_dev> <mint>", "Analyse d'un wallet dev en contexte d'un token"),
        ("tx <adresse>", "Transactions recentes d'une adresse"),
        ("balance <adresse>", "Balance SOL d'un wallet"),
        ("aide", "Afficher cette aide"),
        ("quitter / exit / q", "Quitter l'agent"),
    ]
    for cmd, desc in commands:
        help_table.add_row(cmd, desc)

    console.print(help_table)

    console.print("\n[dim]Vous pouvez aussi poser des questions en langage naturel.[/dim]")
    console.print("[dim]Exemples:[/dim]")
    console.print("[dim]  > Est-ce que ce token est un rug pull? <mint>[/dim]")
    console.print("[dim]  > Montre-moi les holders de <mint>[/dim]")
    console.print("[dim]  > Analyse le wallet du dev <adresse>[/dim]")
