"""Token analysis module - metadata, holders, liquidity, supply."""

from __future__ import annotations

from dataclasses import dataclass, field

from solana_agent.helius_client import HeliusClient


@dataclass
class TokenInfo:
    mint: str = ""
    name: str = ""
    symbol: str = ""
    decimals: int = 0
    supply: float = 0.0
    uri: str = ""
    image: str = ""
    description: str = ""
    # Authorities
    mint_authority: str | None = None
    freeze_authority: str | None = None
    update_authority: str | None = None
    # Holder info
    holder_count: int = 0
    top_holders: list[dict] = field(default_factory=list)
    top_holder_pct: float = 0.0
    # Creator / dev
    creators: list[dict] = field(default_factory=list)
    owner: str = ""
    # Metadata
    mutable: bool = False
    compressed: bool = False
    token_standard: str = ""


class TokenAnalyzer:
    """Analyze Solana tokens for trading intelligence."""

    def __init__(self, client: HeliusClient) -> None:
        self.client = client

    def analyze_token(self, mint: str) -> TokenInfo:
        """Full token analysis: metadata + supply + holders."""
        info = TokenInfo(mint=mint)

        # 1. DAS asset metadata
        try:
            asset = self.client.get_asset(mint)
            self._parse_asset_metadata(asset, info)
        except Exception as e:
            info.name = f"[Erreur metadata: {e}]"

        # 2. Token supply
        try:
            supply_data = self.client.get_token_supply(mint)
            value = supply_data.get("value", {})
            info.supply = float(value.get("uiAmount", 0) or 0)
            info.decimals = int(value.get("decimals", 0))
        except Exception:
            pass

        # 3. Largest holders
        try:
            holders = self.client.get_token_largest_accounts(mint)
            info.top_holders = self._parse_holders(holders, info.supply)
            info.holder_count = len(holders)
            if info.top_holders:
                info.top_holder_pct = sum(h["pct"] for h in info.top_holders[:5])
        except Exception:
            pass

        return info

    def get_holder_distribution(self, mint: str) -> dict:
        """Analyze the holder distribution for concentration risks."""
        holders = self.client.get_token_largest_accounts(mint)
        supply_data = self.client.get_token_supply(mint)
        total_supply = float(supply_data.get("value", {}).get("uiAmount", 1) or 1)

        parsed = self._parse_holders(holders, total_supply)
        top1 = parsed[0]["pct"] if parsed else 0
        top5 = sum(h["pct"] for h in parsed[:5])
        top10 = sum(h["pct"] for h in parsed[:10])
        top20 = sum(h["pct"] for h in parsed[:20])

        return {
            "total_holders_sampled": len(parsed),
            "top_1_pct": round(top1, 2),
            "top_5_pct": round(top5, 2),
            "top_10_pct": round(top10, 2),
            "top_20_pct": round(top20, 2),
            "holders": parsed,
            "concentration_risk": "ELEVE" if top5 > 50 else "MOYEN" if top5 > 30 else "FAIBLE",
        }

    def _parse_asset_metadata(self, asset: dict, info: TokenInfo) -> None:
        content = asset.get("content", {})
        metadata = content.get("metadata", {})
        info.name = metadata.get("name", "Inconnu")
        info.symbol = metadata.get("symbol", "???")
        info.description = metadata.get("description", "")
        info.token_standard = metadata.get("token_standard", "")

        links = content.get("links", {})
        info.image = links.get("image", "")
        info.uri = content.get("json_uri", "")

        authorities = asset.get("authorities", [])
        for auth in authorities:
            scopes = auth.get("scopes", [])
            address = auth.get("address", "")
            if "full" in scopes:
                info.update_authority = address

        info.mutable = asset.get("mutable", False)
        info.compressed = asset.get("compression", {}).get("compressed", False)

        ownership = asset.get("ownership", {})
        info.owner = ownership.get("owner", "")
        info.freeze_authority = ownership.get("frozen", None)

        creators = asset.get("creators", [])
        info.creators = [
            {"address": c.get("address", ""), "share": c.get("share", 0), "verified": c.get("verified", False)}
            for c in creators
        ]

        mint_ext = asset.get("mint_extensions", {})
        if mint_ext:
            mint_auth = mint_ext.get("mint_close_authority", {})
            if mint_auth:
                info.mint_authority = mint_auth.get("close_authority", None)

        token_info = asset.get("token_info", {})
        if token_info:
            info.decimals = token_info.get("decimals", info.decimals)
            info.supply = float(token_info.get("supply", 0)) / (10 ** info.decimals) if info.decimals else info.supply
            info.mint_authority = token_info.get("mint_authority", info.mint_authority)
            info.freeze_authority = token_info.get("freeze_authority", info.freeze_authority)

    def _parse_holders(self, holders: list, total_supply: float) -> list[dict]:
        parsed = []
        for h in holders:
            amount = float(h.get("amount", 0))
            ui_amount = h.get("uiAmount") or h.get("uiAmountString")
            if ui_amount:
                amount_display = float(ui_amount)
            else:
                decimals = h.get("decimals", 0)
                amount_display = amount / (10 ** decimals) if decimals else amount

            pct = (amount_display / total_supply * 100) if total_supply > 0 else 0
            parsed.append({
                "address": h.get("address", ""),
                "amount": amount_display,
                "pct": round(pct, 4),
            })
        parsed.sort(key=lambda x: x["amount"], reverse=True)
        return parsed
