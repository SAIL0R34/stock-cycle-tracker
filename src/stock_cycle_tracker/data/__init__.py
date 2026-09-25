"""Data module for Stock Cycle Tracker.

Handles equity OHLCV ingestion (Alpaca primary, Coinbase crypto fallback),
normalization, source abstraction, and loading historical data.
"""

from .adapters import DataAdapter
from .fetchers import BaseFetcher, CoinbaseFetcher, get_fetcher
from .loaders import DataLoader
from .normalization import normalize_ohlcv

__all__ = [
    "BaseFetcher",
    "CoinbaseFetcher",
    "AlpacaFetcher",
    "get_fetcher",
    "DataLoader",
    "DataAdapter",
    "normalize_ohlcv",
]
