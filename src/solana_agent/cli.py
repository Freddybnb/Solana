"""CLI entry point for the Solana Memecoin Agent."""

from __future__ import annotations

import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console

from solana_agent import formatter
from solana_agent.agent import SolanaMemecoinAgent

console = Console()


def main() -> None:
    """Main CLI loop."""
    formatter.print_banner()
    console.print()

    try:
        agent = SolanaMemecoinAgent()
    except ValueError as e:
        console.print(f"[bold red]Erreur: {e}[/bold red]")
        console.print(
            "[yellow]Configurez votre cle API:[/yellow]\n"
            "  export HELIUS_API_KEY=votre_cle_api\n\n"
            "Obtenez une cle gratuite sur https://dev.helius.xyz/dashboard/app"
        )
        sys.exit(1)

    console.print("[green]Agent connecte a Helius API (Solana Mainnet)[/green]")
    console.print("[dim]Tapez 'aide' pour voir les commandes disponibles, 'q' pour quitter.[/dim]\n")

    session: PromptSession[str] = PromptSession(history=InMemoryHistory())

    try:
        while True:
            try:
                user_input = session.prompt("agent > ")
                if user_input.strip():
                    console.print()
                    agent.process_input(user_input)
                    console.print()
            except KeyboardInterrupt:
                console.print("\n[dim]Ctrl+C detecte. Tapez 'q' pour quitter.[/dim]")
            except EOFError:
                break
            except SystemExit:
                break
            except Exception as e:
                console.print(f"[red]Erreur: {e}[/red]")
    finally:
        agent.close()
        console.print("[dim]Agent arrete. A bientot![/dim]")


if __name__ == "__main__":
    main()
