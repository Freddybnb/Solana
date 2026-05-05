"""Helius API client for Solana blockchain data."""

from __future__ import annotations

import httpx

from solana_agent.config import HELIUS_API_KEY, HELIUS_API_URL


class HeliusClient:
    """Client for interacting with Helius APIs (RPC, DAS, Enhanced Transactions, Wallet)."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or HELIUS_API_KEY
        if not self.api_key:
            raise ValueError(
                "HELIUS_API_KEY non definie. "
                "Exportez-la: export HELIUS_API_KEY=votre_cle"
            )
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
        self.api_url = HELIUS_API_URL
        self.das_url = self.rpc_url
        self._client = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._client.close()

    # ── RPC helpers ──────────────────────────────────────────────────────

    def _rpc_call(self, method: str, params: list | None = None) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or [],
        }
        resp = self._client.post(self.rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data.get("result", {})

    def _das_call(self, method: str, params: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        resp = self._client.post(self.das_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"DAS error: {data['error']}")
        return data.get("result", {})

    # ── Token info ───────────────────────────────────────────────────────

    def get_asset(self, mint_address: str) -> dict:
        """Get full token/NFT metadata via DAS API."""
        return self._das_call("getAsset", {"id": mint_address})

    def get_asset_proof(self, mint_address: str) -> dict:
        return self._das_call("getAssetProof", {"id": mint_address})

    def search_assets(
        self,
        owner: str | None = None,
        group_key: str | None = None,
        group_value: str | None = None,
        page: int = 1,
        limit: int = 100,
    ) -> dict:
        params: dict = {"page": page, "limit": limit}
        if owner:
            params["ownerAddress"] = owner
        if group_key and group_value:
            params["grouping"] = [group_key, group_value]
        return self._das_call("searchAssets", params)

    # ── Wallet / Account ─────────────────────────────────────────────────

    def get_assets_by_owner(
        self, owner: str, page: int = 1, limit: int = 100, show_fungible: bool = True
    ) -> dict:
        """List all tokens (including fungible) owned by a wallet."""
        params: dict = {
            "ownerAddress": owner,
            "page": page,
            "limit": limit,
            "displayOptions": {"showFungible": show_fungible},
        }
        return self._das_call("getAssetsByOwner", params)

    def get_token_accounts(self, owner: str | None = None, mint: str | None = None) -> dict:
        """Get SPL token accounts by owner or mint."""
        params: dict = {}
        if owner:
            params["owner"] = owner
        if mint:
            params["mint"] = mint
        params["limit"] = 100
        return self._das_call("getTokenAccounts", params)

    def get_balance(self, address: str) -> float:
        """Get SOL balance in SOL (not lamports)."""
        result = self._rpc_call("getBalance", [address])
        lamports = result.get("value", 0)
        return lamports / 1e9

    def get_account_info(self, address: str) -> dict:
        return self._rpc_call("getAccountInfo", [address, {"encoding": "jsonParsed"}])

    # ── Transactions ─────────────────────────────────────────────────────

    def get_signatures(self, address: str, limit: int = 20) -> list:
        """Get recent transaction signatures for an address."""
        return self._rpc_call("getSignaturesForAddress", [address, {"limit": limit}])

    def get_parsed_transactions(self, signatures: list[str]) -> list[dict]:
        """Get enhanced parsed transactions from Helius."""
        url = f"{self.api_url}/transactions?api-key={self.api_key}"
        resp = self._client.post(url, json={"transactions": signatures})
        resp.raise_for_status()
        return resp.json()

    def get_transaction(self, signature: str) -> dict:
        """Get a single parsed transaction."""
        return self._rpc_call(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )

    # ── Token supply & holders ───────────────────────────────────────────

    def get_token_supply(self, mint: str) -> dict:
        return self._rpc_call("getTokenSupply", [mint])

    def get_token_largest_accounts(self, mint: str) -> list:
        """Get the 20 largest holders of a token."""
        result = self._rpc_call("getTokenLargestAccounts", [mint])
        return result.get("value", [])
