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

M9.1 fix (D-M9-7): each domain is now written to BigQuery the moment its own
fetch finishes, via sync_domain_to_bigquery() (delete-this-domain-for-this-date,
then append). Previously the only warehouse write was one WRITE_TRUNCATE of the
whole day partition at the end of the run. A partial or interrupted run then
truncated the partition down to whatever it had reached, deleting every other
domain's data — this is what wiped fundly.com and ngpvan.com on 2026-07-27,
leaving only infinitegiving.com. Per-domain writes make a domain durable as soon
as it is scanned and make a partial run incapable of touching domains it did not
fetch. The end-of-run sync_to_bigquery() call is kept as a reconciliation pass
that carries the complete result set.

M9.3 fix (D-M9-14): _domain_account_map is a ContextVar, not a module global.
It was shared by every concurrent request in the process, and
list_gsc_properties() cleared and rebuilt it across `await` points, so two
overlapping users could interleave and query the wrong Google account for a
domain. _live_gsc_fetch() now also populates the mapping for its own request,
because the map is consumed in a different HTTP request (/api/run) from the one
that used to fill it (/api/domains); a per-context variable is not inherited
across requests.
"""
from __future__ import annotations
import asyncio
from contextvars import ContextVar
from datetime import date, timedelta
from typing import Optional, Callable

from .config import FFG_OWNED_DOMAINS, GSC_ROW_LIMIT, GSC_MAX_WORKERS, USE_BIGQUERY
from .util import GscRow, LogFn, map_with_concurrency
from .googleauth import gsc_client
from .bigquery import fetch_from_bigquery, sync_to_bigquery, sync_domain_to_bigquery
from .tokenstore import get_auth_status

# M4 item 2: callback fired once per domain as its live GSC rows finish, so the
# pipeline can overlap metadata crawling with the fetch of later domains.
OnDomainComplete = Callable[[str, list], None]

# ── Domain → account key mapping (M9.3: ContextVar, was a module global) ──────
# FastAPI runs concurrent requests as interleaved coroutines in ONE process, so
# a plain module-level dict here is shared by every user at once.
# list_gsc_properties() cleared and rebuilt that dict with `await` points in
# between, so two overlapping runs could interleave: _account_for_domain() would
# then return another user's mapping, or fall back to "data" for a domain owned
# by analytics@. That is not a crash, it is a silently wrong Google account
# being queried, which is worse.
#
# A ContextVar is per-context. asyncio copies the current context when a Task is
# created, so a value set before `map_with_concurrency` fans out is visible to
# every child task of THAT request, and invisible to other requests.
#
# default is None, never a mutable default: `default={}` would be one dict
# shared by every context, which is exactly the bug being removed. Always .set()
# a freshly built dict, never mutate the stored one in place.
_domain_account_map: ContextVar[Optional[dict[str, str]]] = ContextVar(
    "gsc_domain_account_map", default=None
)


def _account_for_domain(domain: str) -> str:
    current = _domain_account_map.get()
    return (current or {}).get(domain, "data")


async def _ensure_domain_account_map(log: LogFn = print) -> None:
    """
    Make sure THIS request/context has a domain to account mapping.

    Required because the map is consumed in a different HTTP request from the
    one that populates it: GET /api/domains calls list_gsc_properties(), while
    POST /api/run consumes the mapping later, during the fetch. With a module
    global that cross-request coupling happened to work, and was the source of
    the cross-user race. With a per-context ContextVar every request starts
    empty, so the fetch path has to populate its own map. Without this, every
    domain would silently fall back to the "data" account and analytics@-owned
    properties would return no data.

    Building it here also removes the latent ordering dependency: a run no
    longer requires that /api/domains was called first in the same process.

    Best-effort. A failure is logged, and _account_for_domain() falls back to
    "data", which is the same behavior as before this function existed.
    """
    if _domain_account_map.get():
        return
    try:
        await list_gsc_properties(log)
    except Exception as e:
        log(f"Could not resolve the domain to account mapping: {e}")


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
            log(f"{account}: could not list GSC properties. {e}")
            props_by_account[account] = []

    # M9.3: build a NEW dict and .set() it into this context. The old code did
    # _domain_account_map.clear() then filled it in place, which mutated state
    # shared by every concurrent request. Never mutate the stored dict.
    new_map: dict[str, str] = {}
    for account, domains in props_by_account.items():
        for d in domains:
            new_map[d] = account
    _domain_account_map.set(new_map)

    ffg_set = set(FFG_OWNED_DOMAINS)

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
            log(f"BigQuery is unavailable ({e}). Fetching live from Search Console.")

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
    # M9.3: resolve this request's domain to account mapping first. Every
    # _account_for_domain() call below depends on it, and with a per-context
    # ContextVar the mapping is not inherited from the earlier /api/domains
    # request. No-op when the context already has one (the admin path calls
    # list_gsc_properties() itself before fetching).
    await _ensure_domain_account_map(log)

    # M4 item 2: as each domain's rows finish, fire on_domain_complete(domain, rows)
    # so the pipeline can begin crawling that domain's pages while later domains
    # are still being fetched. The callback is best-effort and never blocks or
    # fails the fetch.
    log("▸ Connecting to sites and checking search traffic volumes...")
    live_frames: dict[str, list[GscRow]] = {}
    heavy_domains: list[str] = []

    def _notify(domain: str, rows: list[GscRow]) -> None:
        # M9.1 (D-M9-7): persist THIS domain to BigQuery the moment it finishes,
        # scoped to its own rows so a partial run cannot delete other domains.
        # Scheduled as a background task on the same event loop; a per-domain
        # write failure is swallowed inside sync_domain_to_bigquery (best-effort)
        # and the end-of-run reconciliation pass is the backstop. Guarded by
        # USE_BIGQUERY so live-only deployments are unaffected.
        if USE_BIGQUERY and rows:
            try:
                asyncio.create_task(sync_domain_to_bigquery(domain, rows, log))
            except Exception:
                pass  # never let the durable write break the fetch

        # M4 item 2: metadata pre-warm overlap — unchanged.
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
                log(f"{short}: no search data for these dates.")
            elif len(probe) >= 25000:
                log(f"{short}: reading high-volume history. This one takes longer.")
                heavy_domains.append(d)
            else:
                log(f"{short}: scanned.")
                live_frames[d] = probe
                _notify(d, probe)  # M4 item 2: light domain done → enqueue for crawl
        except Exception as e:
            log(f"{short}: failed. {e}")

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
                log(f"{short}: no search data found.")
            else:
                live_frames[d] = _aggregate_raw(lst)
                log(f"{short}: scanned.")
                _notify(d, live_frames[d])  # M4 item 2: heavy domain done → enqueue

    final: list[GscRow] = []
    freshly_fetched: list[GscRow] = []
    for frame in live_frames.values():
        final.extend(frame)
        freshly_fetched.extend(frame)

    # M9.1 (D-M9-7): each domain was already written durably and independently
    # as it finished, via sync_domain_to_bigquery() in _notify(). This final
    # call is the RECONCILIATION pass — it rebuilds the whole partition from the
    # complete result set as a consistency backstop. It is only safe because
    # freshly_fetched here is the COMPLETE set for this run; never call
    # sync_to_bigquery with a partial set (that is the failure mode that caused
    # the 2026-07-27 data loss). Any in-flight per-domain background writes are
    # allowed to settle first so the reconciliation does not race them.
    if USE_BIGQUERY and freshly_fetched:
        await asyncio.sleep(0)  # let scheduled per-domain write tasks start
        await sync_to_bigquery(freshly_fetched, log)

    return final
