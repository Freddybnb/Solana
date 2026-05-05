# Solana Memecoin Agent

Agent IA expert en trading de memecoins sur la blockchain Solana. Scanner la blockchain via l'API Helius pour detecter les rug pulls, analyser les tokens et les wallets.

## Fonctionnalites

- **Analyse de tokens** : Metadata, supply, holders, autorites (mint/freeze)
- **Detection de rug pulls** : Scoring de risque multi-criteres (0-100)
- **Detection de pump & dump** : Concentration des holders, transactions suspectes
- **Analyse des wallets dev** : Pattern detection, serial deployer, holdings
- **Distribution des holders** : Top holders, concentration, whales
- **Suivi des transactions** : Historique, changements de balance, programmes utilises
- **Langage naturel** : Posez vos questions en francais ou anglais

## Types de scams detectes

| Type | Description | Detection |
|------|-------------|-----------|
| **Rug Pull** | Dev retire la liquidite | Mint authority, concentration, liquidity |
| **Honeypot** | Impossible de vendre | Freeze authority active |
| **Pump & Dump** | Achat coordonne puis dump | Volume, holder concentration |
| **Slow Rug** | Vente progressive du dev | Transaction patterns |
| **Bundled Launch** | Dev achete avec plusieurs wallets | Linked wallets |

## Installation

```bash
# Cloner le repo
git clone <repo_url>
cd solana-memecoin-agent

# Installer les dependances
pip install -e .

# Configurer la cle API Helius
export HELIUS_API_KEY=votre_cle_api
```

Obtenez une cle API gratuite sur [Helius Dashboard](https://dev.helius.xyz/dashboard/app).

## Utilisation

```bash
# Lancer l'agent
solana-agent
```

### Commandes

| Commande | Description |
|----------|-------------|
| `analyse <mint>` | Analyse complete d'un token |
| `risque <mint>` | Analyse de risque / detection rug pull |
| `holders <mint>` | Distribution des holders |
| `wallet <adresse>` | Profil complet d'un wallet |
| `dev <adresse_dev> <mint>` | Analyse d'un wallet dev pour un token |
| `tx <adresse>` | Transactions recentes |
| `balance <adresse>` | Balance SOL |
| `aide` | Afficher l'aide |

### Langage naturel

```
agent > Est-ce que ce token est un rug? EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
agent > Montre-moi les holders de So11111111111111111111111111111111
agent > Analyse le wallet du dev 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU
```

## Criteres de risque

| Signal | Poids | Description |
|--------|-------|-------------|
| Mint Authority actif | 25 | Le dev peut creer de nouveaux tokens |
| Freeze Authority actif | 15 | Le dev peut geler les comptes |
| Concentration holders | 20 | Top 5 wallets > 30% de la supply |
| Faible liquidite | 15 | Liquidite < 5000 USD |
| Peu de holders | 10 | Moins de 50 holders |
| Holdings du dev | 15 | Dev detient > 10% de la supply |

### Niveaux de risque

- **SAFE** (0-14) : Aucun signal majeur
- **FAIBLE** (15-29) : Points mineurs
- **MOYEN** (30-49) : Vigilance recommandee
- **ELEVE** (50-69) : Risque important
- **CRITIQUE** (70-100) : Probablement un scam

## Architecture

```
src/solana_agent/
  __init__.py          # Package init
  config.py            # Configuration et seuils
  helius_client.py     # Client API Helius (RPC, DAS, Enhanced Tx)
  token_analyzer.py    # Analyse de tokens
  rug_detector.py      # Detection de rug pulls
  wallet_analyzer.py   # Analyse des wallets
  agent.py             # Logique de l'agent IA
  formatter.py         # Affichage Rich (tables, panels)
  cli.py               # Interface CLI
```

## API Helius utilisees

- **RPC** : getBalance, getTokenSupply, getTokenLargestAccounts, getSignaturesForAddress
- **DAS** : getAsset, getAssetsByOwner, getTokenAccounts, searchAssets
- **Enhanced Transactions** : /v0/transactions (parsed transactions)

## Licence

MIT
