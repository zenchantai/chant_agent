from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from typing import Any, Callable

from .providers import SUPPLEMENTAL_SECURITIES, fetch_security_catalog, fetch_security_profile, normalize_security_symbol

# BaoStock's daily catalogue normally contains more than 7,000 A-share stocks,
# ETFs and other exchange instruments. A smaller response is usually a partial
# upstream result and must not be persisted as a complete search catalogue.
MIN_COMPLETE_CATALOG_COUNT = 6000

SEARCH_ALIASES = {
    "000001": ("1A0001",), "000016": ("1A0016",), "000300": ("1A0300",),
    "000688": ("1A0688",), "000680": ("1A0680",), "000852": ("1A0852",),
    "399001": ("399001",), "399006": ("399006",), "399673": ("399673",),
    "上证": ("1A0001", "1A0016"), "上证指数": ("1A0001",), "上证综指": ("1A0001",),
    "科创50": ("1A0688",), "科创板50": ("1A0688",), "科创综指": ("1A0680",),
    "沪深300": ("1A0300",), "中证1000": ("1A0852",), "深圳成指": ("399001",),
    "深证成指": ("399001",), "创业板": ("399006", "399673"), "创业板指": ("399006",),
}


class SecurityCatalogService:
    def __init__(self, store,
                 catalog_fetcher: Callable[[], dict[str, Any]] = fetch_security_catalog,
                 profile_fetcher: Callable[[str], dict[str, Any] | None] = fetch_security_profile):
        self.store = store
        self.catalog_fetcher = catalog_fetcher
        self.profile_fetcher = profile_fetcher
        self._refresh_lock = threading.Lock()

    def refresh_if_stale(self, force: bool = False) -> dict[str, Any]:
        current = self.store.security_catalog_meta()
        refreshed_today = bool(
            (current.get("refreshed_at") or "")[:10] == date.today().isoformat()
        )
        if int(current.get("count") or 0) >= MIN_COMPLETE_CATALOG_COUNT and refreshed_today and not force:
            return current
        with self._refresh_lock:
            current = self.store.security_catalog_meta()
            if int(current.get("count") or 0) >= MIN_COMPLETE_CATALOG_COUNT and (current.get("refreshed_at") or "")[:10] == date.today().isoformat() and not force:
                return current
            fetched = self.catalog_fetcher()
            self.store.replace_security_catalog(fetched["items"], fetched["catalog_date"])
            return self.store.security_catalog_meta()

    def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        query = query.strip()
        if len(query) < 2:
            return {"query": query, "items": [], **self.store.security_catalog_meta()}
        self.refresh_if_stale()
        items = self.store.search_security_catalog(query, limit)
        # Name conventions differ across market terminals (e.g. “上证指数”
        # vs “上证综合指数”). Resolve well-known aliases without replacing
        # the provider's canonical name.
        alias_symbols = SEARCH_ALIASES.get(query) or ()
        if alias_symbols:
            alias_items = []
            known = {item["symbol"] for item in items}
            selected_symbols = {item["symbol"] for item in self.store.list_stock_pool()}
            for alias in alias_symbols:
                item = self.store.security_catalog_by_symbol(alias)
                if item:
                    item["selected"] = alias in selected_symbols
                    alias_items.append(item)
                    items = [candidate for candidate in items if candidate["symbol"] != alias]
            items = alias_items + items
            items = items[:limit]
        try:
            exact_symbol = normalize_security_symbol(query)
        except ValueError:
            exact_symbol = None
        if not items and exact_symbol:
            profile = self.profile_fetcher(exact_symbol)
            if profile:
                meta = self.store.security_catalog_meta()
                self.store.upsert_security_catalog(profile, meta.get("catalog_date") or date.today().isoformat())
                items = self.store.search_security_catalog(query, limit)
        return {"query": query, "items": items, **self.store.security_catalog_meta()}

    def resolve(self, symbol: str) -> dict[str, Any] | None:
        symbol = normalize_security_symbol(symbol)
        self.refresh_if_stale()
        candidate = self.store.security_catalog_by_symbol(symbol)
        if candidate:
            return candidate
        profile = self.profile_fetcher(symbol)
        if profile:
            meta = self.store.security_catalog_meta()
            self.store.upsert_security_catalog(profile, meta.get("catalog_date") or date.today().isoformat())
        return self.store.security_catalog_by_symbol(symbol)
