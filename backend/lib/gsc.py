"""
gsc.py — GSC fetch engine, BigQuery-first.
Port of gsc.ts. Preserves D12 (freshness gate gates the short-circuit return).

M7 fixes:
  - list_gsc_properties: filter to sc-domain: properties only (strips URL-prefix
    properties that were appearing in the domain picker alongside domain properties)
  - _execute_with_backoff: asyncio.get_event_loop() → asyncio.get_running_loop()
    (get_event_loop() is deprecated in Python 3.10+ inside async context)
  - _live_gsc_fetch heavy-domain path: build one GSC client per account key before
    the chunk loop and pass it through, eliminating one Upstash round-trip + one
    blocking gapi_build() call per chunk (was 13+ redundant calls for a 12-month
    date range, the primary cause of 10+ minute heavy-domain fetches)
"""
from __future__ import annotations
import asyncio
from datetime import date, timedelta
from typing import Optional, Callable

from .config import FFG_OWNED_DOMAINS, GSC_ROW_LIMIT, GSC_MAX_WORKERS, USE_BIGQUERY
from .util import GscRow, LogFn, map_with_concurrency
from .googleauth import gsc_client
from .bigquery import fetch_from_bigquery, sync_to_bigquery
from .tokenstore import get_auth_status

# M4 item 2: callback fired once per domain as its live GSC rows finish, so the
# pipeline can overlap metadata crawling with the fetch of later domains.
OnDomainComplete = Callable[[str, list], None]

# Domain → account key mapping, populated by list_gsc_properties().
_domain_account_map: dict[str, str] = {}


def _account_for_domain(domain: str) -> str:
    return _domain_account_map.get(domain, "data")


async def list_gsc_properties(log: LogFn = print) -> dict:
    status = await get_auth_status()
    accounts = [k for k, v in status.items() if v]
    props_by_account: dict[str, list[str]] = {}

    for account in accounts:
        try:
            client = await gsc_client(account)
            resp = client.sites().list().execute()
            sites = resp.get("siteEntry", [])
            props_by_account[account] = [
                s["siteUrl"] for s in sites
                # M7 fix 1: sc-domain: filter — keep domain properties only.
                # The GSC API returns both sc-domain:example.com (domain property)
                # and https://example.com/ (URL-prefix property) for the same site.
                # URL-prefix properties must be excluded at the source; they cannot
                # be used interchangeably with domain properties in the pipeline.
                if s["siteUrl"].startswith("sc-domain:")
                and s.get("permissionLevel") in ("siteOwner", "siteFullUser", "siteRestrictedUser")
            ]
        except Exception as e:
            # D-S5: surface env/auth/network errors in the streaming console instead
            # of silently returning 0 domains (the proximate cause of the M3.5
            # "0 domains" bug when GOOGLE_CLIENT_ID was unloaded).
            log(f"! {account}@ — GSC property list failed: {e}")
            props_by_account[account] = []

    _domain_account_map.clear()
    ffg_set = set(FFG_OWNED_DOMAINS)
    for account, domains in props_by_account.items():
        for d in domains:
            _domain_account_map[d] = account

    all_domains: set[str] = set()
    for domains in props_by_account.values():
        all_domains.update(domains)

    ffg_ordered = [d for d in FFG_OWNED_DOMAINS if d in all_domains]
    client_ordered = sorted(d for d in all_domains if d not in ffg_set)

    return {
        "props_by_account": props_by_account,
        "ordered": ffg_ordered + client_ordered,
    }


async def _execute_with_backoff(client, site_url: str, body: dict, max_retries: int = 5) -> Optional[dict]:
    for n in range(max_retries):
        try:
            resp = client.searchanalytics().query(siteUrl=site_url, body=body).execute()
            return resp
        except Exception as e:
            status = getattr(e, "resp", None)
            code = int(status.status) if status else 0
            if code in (429, 500, 502, 503, 504):
                # M7 fix 2: asyncio.get_running_loop() replaces deprecated
                # asyncio.get_event_loop() — the latter raises DeprecationWarning
                # in Python 3.10+ when called inside a running async context.
                jitter = asyncio.get_running_loop().time() % 1
                await asyncio.sleep((2 ** n) + jitter)
            else:
                return None
    return None


def _rows_from_resp(account: str, domain: str, data: dict) -> list[GscRow]:
    return [
        GscRow(
            account=account, domain=domain,
            query=r.get("keys", [""])[0],
            page=r.get("keys", ["", ""])[1] if len(r.get("keys", [])) > 1 else "",
            clicks=int(r.get("clicks", 0)),
            impressions=int(r.get("impressions", 0)),
            position=float(r.get("position", 0)),
        )
        for r in data.get("rows", [])
    ]


async def _probe_domain(domain: str, start_date: str, end_date: str) -> list[GscRow]:
    key = _account_for_domain(domain)
    client = await gsc_client(key)
    body = {"startDate": start_date, "endDate": end_date, "dimensions": ["query", "page"], "rowLimit": 25000, "startRow": 0}
    data = await _execute_with_backoff(client, domain, body)
    return _rows_from_resp(key, domain, data) if data else []


async def _fetch_domain_chunk_with_client(
    client, account: str, domain: str, c_start: str, c_end: str
) -> list[GscRow]:
    """
    Fetch one 30-day chunk for a heavy domain using a pre-built client.

    M7 fix 3: accepts a pre-built client instead of calling gsc_client() per
    chunk. A 12-month date range produces ~13 chunks per heavy domain. The old
    _fetch_domain_chunk() called gsc_client() on every chunk, paying one Upstash
    GET round-trip + one blocking gapi_build() discovery-document call per chunk.
    With 2 heavy domains that was 26 redundant round-trips — the primary cause of
    10+ minute fetch times.
    """
    all_rows: list[GscRow] = []
    body = {
        "startDate": c_start, "endDate": c_end,
        "dimensions": ["query", "page"], "rowLimit": 25000, "startRow": 0,
    }
    while True:
        data = await _execute_with_backoff(client, domain, body)
        if not data:
            break
        batch = _rows_from_resp(account, domain, data)
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < 25000:
            break
        body["startRow"] += len(batch)
    return all_rows


def _aggregate_raw(rows: list[GscRow]) -> list[GscRow]:
    groups: dict[str, dict] = {}
    for r in rows:
        k = f"{r.account}|{r.domain}|{r.query}|{r.page}"
        if k not in groups:
            groups[k] = {
                "account": r.account, "domain": r.domain,
                "query": r.query, "page": r.page,
                "clicks": 0, "impressions": 0, "pos_x_imp": 0.0,
            }
        g = groups[k]
        g["clicks"] += r.clicks
        g["impressions"] += r.impressions
        g["pos_x_imp"] += r.position * r.impressions

    out: list[GscRow] = []
    for g in groups.values():
        out.append(GscRow(
            account=g["account"], domain=g["domain"],
            query=g["query"], page=g["page"],
            clicks=g["clicks"], impressions=g["impressions"],
            position=round(g["pos_x_imp"] / g["impressions"], 2) if g["impressions"] > 0 else 0.0,
        ))
    out.sort(key=lambda r: (-r.clicks, -r.impressions))
    return out[:GSC_ROW_LIMIT]


def _date_chunks(start_date: str, end_date: str) -> list[tuple[str, str]]:
    chunks = []
    current = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while current <= end:
        chunk_end = min(current + timedelta(days=29), end)
        chunks.append((current.isoformat(), chunk_end.isoformat()))
        current = chunk_end + timedelta(days=1)
    return chunks


async def fetch_all_domains(
    domains: list[str],
    start_date: str,
    end_date: str,
    log: LogFn = print,
    on_domain_complete: "OnDomainComplete | None" = None,
) -> list[GscRow]:
    # ── Primary path: BigQuery warehouse (D12 freshness gate) ────────────────
    if USE_BIGQUERY:
        try:
            log("▸ Reading from the BigQuery warehouse...")
            rows, fresh = await fetch_from_bigquery(domains, start_date, end_date, log)
            if rows and fresh:
                # Warm warehouse: fetch returns immediately. The M4 overlap
                # (item 2) is moot here — no callback fires, no live fetch.
                return rows
            log(
                "! Warehouse data is stale — refreshing live from Search Console."
                if rows else "! Warehouse empty — fetching live from Search Console."
            )
        except Exception as e:
            log(f"! BigQuery unavailable ({e}) — fetching live from Search Console.")

    return await _live_gsc_fetch(domains, start_date, end_date, log, on_domain_complete)


async def fetch_all_domains_live(
    domains: list[str],
    start_date: str,
    end_date: str,
    log: LogFn = print,
    on_domain_complete: "OnDomainComplete | None" = None,
) -> list[GscRow]:
    """
    Admin-only path: always hits the live GSC API, never short-circuits on a
    warm warehouse. Used by the admin sync-gsc job (D-S3 boundary).
    Writes results to BigQuery when USE_BIGQUERY is set (D-S2).
    """
    return await _live_gsc_fetch(domains, start_date, end_date, log, on_domain_complete)


async def _live_gsc_fetch(
    domains: list[str],
    start_date: str,
    end_date: str,
    log: LogFn = print,
    on_domain_complete: "OnDomainComplete | None" = None,
) -> list[GscRow]:
    # ── Live GSC fetch ────────────────────────────────────────────────────────
    # M4 item 2: as each domain's rows finish, fire on_domain_complete(domain, rows)
    # so the pipeline can begin crawling that domain's pages while later domains
    # are still being fetched. The callback is best-effort and never blocks or
    # fails the fetch.
    log("▸ Connecting to sites and checking search traffic volumes...")
    live_frames: dict[str, list[GscRow]] = {}
    heavy_domains: list[str] = []

    def _notify(domain: str, rows: list[GscRow]) -> None:
        if on_domain_complete and rows:
            try:
                on_domain_complete(domain, rows)
            except Exception:
                pass  # overlap is an optimization — never let it break the fetch

    async def probe_one(d: str) -> None:
        short = d.replace("sc-domain:", "")
        try:
            probe = await _probe_domain(d, start_date, end_date)
            if not probe:
                log(f"! {short} — No search data found for these dates.")
            elif len(probe) >= 25000:
                log(f"▸ {short} — Reading high-volume history (this may take a moment)...")
                heavy_domains.append(d)
            else:
                log(f"✓ {short} — Successfully scanned.")
                live_frames[d] = probe
                _notify(d, probe)  # M4 item 2: light domain done → enqueue for crawl
        except Exception as e:
            log(f"! {short} — Error: {e}")

    await map_with_concurrency(domains, GSC_MAX_WORKERS, probe_one)

    if heavy_domains:
        all_chunks = _date_chunks(start_date, end_date)

        # M7 fix 3: build one GSC client per account key here, before the chunk
        # loop. Each heavy domain's chunks share the same pre-built client, so
        # the Upstash round-trip and gapi_build() discovery call happen once per
        # account key total — not once per chunk.
        account_keys_needed = {_account_for_domain(d) for d in heavy_domains}
        clients_by_key: dict[str, object] = {}
        for key in account_keys_needed:
            try:
                clients_by_key[key] = await gsc_client(key)
            except Exception as e:
                log(f"! Could not build GSC client for {key}@: {e}")

        raw: dict[str, list[GscRow]] = {d: [] for d in heavy_domains}

        async def fetch_chunk(task: tuple) -> None:
            d, cs, ce = task
            key = _account_for_domain(d)
            client = clients_by_key.get(key)
            if not client:
                return
            try:
                chunk = await _fetch_domain_chunk_with_client(client, key, d, cs, ce)
                if chunk:
                    raw[d].extend(chunk)
            except Exception:
                pass

        tasks = [(d, cs, ce) for d in heavy_domains for cs, ce in all_chunks]
        await map_with_concurrency(tasks, GSC_MAX_WORKERS, fetch_chunk)

        for d in heavy_domains:
            short = d.replace("sc-domain:", "")
            lst = raw[d]
            if not lst:
                log(f"! {short} — No search data found.")
            else:
                live_frames[d] = _aggregate_raw(lst)
                log(f"✓ {short} — Successfully scanned.")
                _notify(d, live_frames[d])  # M4 item 2: heavy domain done → enqueue

    final: list[GscRow] = []
    freshly_fetched: list[GscRow] = []
    for frame in live_frames.values():
        final.extend(frame)
        freshly_fetched.extend(frame)

    if USE_BIGQUERY and freshly_fetched:
        await sync_to_bigquery(freshly_fetched, log)

    return final