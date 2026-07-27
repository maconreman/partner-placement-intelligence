"""routers/domains.py — GET /api/domains"""
from fastapi import APIRouter
from ..lib.gsc import list_gsc_properties
from ..lib.config import FFG_OWNED_DOMAINS, EXCLUDED_DOMAINS, CLIENT_VERTICAL_MAP

router = APIRouter(prefix="/api")


def _to_host(site_url: str) -> str:
    """Normalise a GSC property identifier to a bare hostname for exclusion matching.

    Strips sc-domain: prefix, URL scheme, www. prefix, and any path so that
    every variant of the same site collapses to one comparable string:
      sc-domain:example.com    →  example.com
      https://example.com/     →  example.com
      https://www.example.com/ →  example.com
    """
    s = site_url.lower().strip()
    if s.startswith("sc-domain:"):
        s = s[len("sc-domain:"):]
    if s.startswith("https://"):
        s = s[len("https://"):]
    elif s.startswith("http://"):
        s = s[len("http://"):]
    if s.startswith("www."):
        s = s[4:]
    s = s.split("/")[0]
    return s


@router.get("/domains")
async def get_domains():
    try:
        result = await list_gsc_properties()
        ffg_set = set(FFG_OWNED_DOMAINS)
        domains = [
            {
                "siteUrl": d,
                "short": d.replace("sc-domain:", ""),
                "isFfg": d in ffg_set,
                # vertical: "FFG" for owned domains, looked up from CLIENT_VERTICAL_MAP
                # for clients, falls back to "Other" for any client not yet in the map.
                "vertical": "FFG" if d in ffg_set else CLIENT_VERTICAL_MAP.get(_to_host(d), "Other"),
            }
            for d in result["ordered"]
            # Filter excluded sites server-side so they never reach the UI.
            # _to_host normalises sc-domain:, https://, www. variants before matching.
            if _to_host(d) not in EXCLUDED_DOMAINS
        ]
        return {"domains": domains}
    except Exception as e:
        return {"error": str(e)}
