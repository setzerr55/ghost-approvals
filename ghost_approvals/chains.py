"""EVM chain registry for Alchemy multi-chain support."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chain:
    key: str
    name: str
    chain_id: int
    alchemy_subdomain: str
    explorer_tx: str
    explorer_addr: str
    native_symbol: str


CHAINS: dict[str, Chain] = {
    "eth": Chain(
        key="eth",
        name="Ethereum",
        chain_id=1,
        alchemy_subdomain="eth-mainnet",
        explorer_tx="https://etherscan.io/tx/",
        explorer_addr="https://etherscan.io/address/",
        native_symbol="ETH",
    ),
    "base": Chain(
        key="base",
        name="Base",
        chain_id=8453,
        alchemy_subdomain="base-mainnet",
        explorer_tx="https://basescan.org/tx/",
        explorer_addr="https://basescan.org/address/",
        native_symbol="ETH",
    ),
    "arb": Chain(
        key="arb",
        name="Arbitrum",
        chain_id=42161,
        alchemy_subdomain="arb-mainnet",
        explorer_tx="https://arbiscan.io/tx/",
        explorer_addr="https://arbiscan.io/address/",
        native_symbol="ETH",
    ),
    "opt": Chain(
        key="opt",
        name="Optimism",
        chain_id=10,
        alchemy_subdomain="opt-mainnet",
        explorer_tx="https://optimistic.etherscan.io/tx/",
        explorer_addr="https://optimistic.etherscan.io/address/",
        native_symbol="ETH",
    ),
    "polygon": Chain(
        key="polygon",
        name="Polygon",
        chain_id=137,
        alchemy_subdomain="polygon-mainnet",
        explorer_tx="https://polygonscan.com/tx/",
        explorer_addr="https://polygonscan.com/address/",
        native_symbol="POL",
    ),
    "bnb": Chain(
        key="bnb",
        name="BNB Chain",
        chain_id=56,
        alchemy_subdomain="bnb-mainnet",
        explorer_tx="https://bscscan.com/tx/",
        explorer_addr="https://bscscan.com/address/",
        native_symbol="BNB",
    ),
}


DEFAULT_CHAINS: tuple[str, ...] = ("eth", "base", "arb", "opt", "polygon", "bnb")


def alchemy_rpc_url(chain_key: str, api_key: str) -> str:
    chain = CHAINS[chain_key]
    return f"https://{chain.alchemy_subdomain}.g.alchemy.com/v2/{api_key}"
