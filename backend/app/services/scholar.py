"""Scholarly search, ported from TheResearcher and trimmed.

Sources (all keyless): Crossref, Semantic Scholar, OpenAlex, arXiv. Results are merged, de-duplicated
by DOI/title, scored by query relevance + citations + recency, and rendered as a numbered evidence
block the seats can cite as [n]. Nothing here is cached across sessions (stateless by design); the
caller pastes the block into the session context.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

import httpx

USER_AGENT = "TheHive/0.3 (+https://github.com/SyphenAI/TheHive)"
TIMEOUT = 20.0

# Optional keyed research providers. Stored in the same encrypted vault as model keys (api_tokens).
RESEARCH_PROVIDERS = {
    "semantic_scholar": {
        "display": "Semantic Scholar (API key)",
        "note": "Free key from semanticscholar.org raises your rate limit above the shared anonymous pool.",
        "signup": "https://www.semanticscholar.org/product/api#api-key-form",
        "unlocks": "faster, more reliable Semantic Scholar results",
    },
    "serpapi": {
        "display": "SerpAPI (Google Scholar)",
        "note": "Paid. Adds Google Scholar as a fifth source.",
        "signup": "https://serpapi.com/",
        "unlocks": "google_scholar source",
    },
}


# ---------- scoring ----------
def _terms(query: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]{3,}", query.lower()) if t not in {"the", "and", "for", "with", "from"}]


def _relevance(title: str, abstract: str, query: str) -> float:
    terms = _terms(query)
    if not terms:
        return 0.0
    t, a = title.lower(), abstract.lower()
    hits = sum(1.0 for w in terms if w in t) * 1.5 + sum(1.0 for w in terms if w in a) * 0.5
    return hits / (len(terms) * 2.0)


def _score(item: dict[str, Any], query: str) -> float:
    rel = _relevance(item.get("title") or "", item.get("abstract") or "", query)
    cites = float(item.get("cited_by_count") or 0)
    cite_boost = min(2.5, (cites + 1) ** 0.35 / 3.0)
    try:
        y = int(item.get("year") or 0)
    except ValueError:
        y = 0
    year_boost = 0.25 if y >= 2022 else 0.1 if y >= 2018 else 0.0
    return round(rel * 3.0 + cite_boost + year_boost, 4)


def _key(item: dict[str, Any]) -> str:
    doi = (item.get("doi") or "").lower().strip()
    if doi:
        return f"doi:{doi}"
    return "t:" + re.sub(r"[^a-z0-9]+", "", (item.get("title") or "").lower())[:80]


def _get(url: str, headers: dict | None = None) -> httpx.Response | None:
    try:
        with httpx.Client(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT, **(headers or {})}, follow_redirects=True
        ) as c:
            r = c.get(url)
        return r if r.status_code < 400 else None
    except httpx.HTTPError:
        return None


def _authors_str(authors: list[str]) -> str:
    return ", ".join(authors[:4]) + (" et al." if len(authors) > 4 else "")


# ---------- sources ----------
def search_crossref(
    q: str, limit: int, year_from: int | None, year_to: int | None, keys: dict | None = None
) -> list[dict]:
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}-01-01")
    if year_to:
        filters.append(f"until-pub-date:{year_to}-12-31")
    fq = f"&filter={quote(','.join(filters))}" if filters else ""
    r = _get(
        f"https://api.crossref.org/works?query={quote(q)}&rows={limit}&select=DOI,title,author,published-print,"
        f"published-online,container-title,abstract,URL,is-referenced-by-count,type{fq}&mailto=thehive@localhost"
    )
    if not r:
        return []
    out = []
    for it in (r.json().get("message") or {}).get("items") or []:
        title = (it.get("title") or [""])[0]
        if not title:
            continue
        authors = [" ".join(x for x in [a.get("given"), a.get("family")] if x) for a in it.get("author") or []]
        year = ""
        for k in ("published-print", "published-online"):
            parts = ((it.get(k) or {}).get("date-parts") or [[]])[0]
            if parts:
                year = str(parts[0])
                break
        abstract = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", it.get("abstract") or "")).strip()
        doi = (it.get("DOI") or "").strip()
        out.append(
            {
                "source": "crossref",
                "title": title,
                "authors": [a for a in authors if a],
                "year": year,
                "venue": (it.get("container-title") or [""])[0],
                "abstract": abstract[:600],
                "doi": doi,
                "url": it.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
                "cited_by_count": int(it.get("is-referenced-by-count") or 0),
            }
        )
    return out


def search_semantic_scholar(
    q: str, limit: int, year_from: int | None, year_to: int | None, keys: dict | None = None
) -> list[dict]:
    yq = ""
    if year_from or year_to:
        yq = f"&year={year_from or ''}-{year_to or ''}"
    hdr = {"x-api-key": keys["semantic_scholar"]} if keys and keys.get("semantic_scholar") else None
    r = _get(
        f"https://api.semanticscholar.org/graph/v1/paper/search?query={quote(q)}&limit={limit}"
        f"&fields=title,authors,year,abstract,url,citationCount,externalIds,venue{yq}",
        headers=hdr,
    )
    if not r:
        return []
    out = []
    for it in r.json().get("data") or []:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        authors = [a.get("name") for a in it.get("authors") or [] if a.get("name")]
        doi = ((it.get("externalIds") or {}).get("DOI") or "").strip()
        out.append(
            {
                "source": "semantic_scholar",
                "title": title,
                "authors": authors,
                "year": str(it.get("year") or ""),
                "venue": it.get("venue") or "",
                "abstract": (it.get("abstract") or "").strip()[:600],
                "doi": doi,
                "url": it.get("url") or (f"https://doi.org/{doi}" if doi else ""),
                "cited_by_count": int(it.get("citationCount") or 0),
            }
        )
    return out


def search_openalex(
    q: str, limit: int, year_from: int | None, year_to: int | None, keys: dict | None = None
) -> list[dict]:
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    fq = f"&filter={quote(','.join(filters))}" if filters else ""
    r = _get(
        f"https://api.openalex.org/works?search={quote(q)}&per_page={limit}&select=id,doi,title,authorships,"
        f"publication_year,cited_by_count,primary_location,abstract_inverted_index,type{fq}"
    )
    if not r:
        return []
    out = []
    for it in r.json().get("results") or []:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        authors = [((a.get("author") or {}).get("display_name") or "").strip() for a in it.get("authorships") or []]
        doi = (it.get("doi") or "").replace("https://doi.org/", "").strip()
        loc = it.get("primary_location") or {}
        abstract = ""
        inv = it.get("abstract_inverted_index")
        if isinstance(inv, dict) and len(inv) < 400:
            pos = sorted((i, w) for w, idxs in inv.items() for i in idxs)
            abstract = " ".join(w for _, w in pos)[:600]
        out.append(
            {
                "source": "openalex",
                "title": title,
                "authors": [a for a in authors if a],
                "year": str(it.get("publication_year") or ""),
                "venue": ((loc.get("source") or {}).get("display_name") or ""),
                "abstract": abstract,
                "doi": doi,
                "url": loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else it.get("id") or ""),
                "cited_by_count": int(it.get("cited_by_count") or 0),
            }
        )
    return out


def search_arxiv(
    q: str, limit: int, year_from: int | None, year_to: int | None, keys: dict | None = None
) -> list[dict]:
    r = _get(f"https://export.arxiv.org/api/query?search_query=all:{quote(q)}&max_results={limit}&sortBy=relevance")
    if not r:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return []
    out = []
    for e in root.findall("a:entry", ns):
        title = re.sub(r"\s+", " ", (e.findtext("a:title", "", ns) or "")).strip()
        if not title:
            continue
        year = (e.findtext("a:published", "", ns) or "")[:4]
        try:
            yi = int(year)
            if (year_from and yi < year_from) or (year_to and yi > year_to):
                continue
        except ValueError:
            pass
        authors = [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)]
        doi = (e.findtext("ar:doi", "", ns) or "").strip()
        url = (e.findtext("a:id", "", ns) or "").strip()
        out.append(
            {
                "source": "arxiv",
                "title": title,
                "authors": [a for a in authors if a],
                "year": year,
                "venue": "arXiv",
                "abstract": re.sub(r"\s+", " ", e.findtext("a:summary", "", ns) or "").strip()[:600],
                "doi": doi,
                "url": url,
                "cited_by_count": 0,
            }
        )
    return out


def _parse_gs_summary(summary: str) -> tuple[str, str, str]:
    """'A Author, B Author - Venue, 2021 - site' -> (authors, year, venue)."""
    parts = [x.strip() for x in summary.split(" - ")]
    authors = parts[0] if parts else ""
    year, venue = "", ""
    if len(parts) > 1:
        m = re.search(r"(19|20)\d{2}", parts[1])
        year = m.group(0) if m else ""
        venue = re.sub(r",?\s*(19|20)\d{2}.*$", "", parts[1]).strip()
    return authors, year, venue


def search_google_scholar(
    q: str, limit: int, year_from: int | None, year_to: int | None, keys: dict | None = None
) -> list[dict]:
    key = (keys or {}).get("serpapi", "")
    if not key:
        return []
    params = {"engine": "google_scholar", "q": q, "api_key": key, "num": max(1, min(limit, 20)), "hl": "en"}
    if year_from:
        params["as_ylo"] = year_from
    if year_to:
        params["as_yhi"] = year_to
    try:
        with httpx.Client(timeout=35.0, headers={"User-Agent": USER_AGENT}) as c:
            r = c.get("https://serpapi.com/search.json", params=params)
        if r.status_code >= 400:
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("error"):
        return []
    out = []
    for it in data.get("organic_results") or []:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        pub = it.get("publication_info") or {}
        names = [a.get("name") for a in (pub.get("authors") or []) if isinstance(a, dict) and a.get("name")]
        a_sum, year, venue = _parse_gs_summary(str(pub.get("summary") or ""))
        authors = names or [a.strip() for a in a_sum.split(",") if a.strip()]
        link = (it.get("link") or "").strip()
        blob = " ".join(
            [
                link,
                it.get("snippet") or "",
                *[str(x.get("link") or "") for x in it.get("resources") or [] if isinstance(x, dict)],
            ]
        )
        m = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", blob, flags=re.I)
        doi = m.group(0).rstrip(").,;") if m else ""
        try:
            cited = int(((it.get("inline_links") or {}).get("cited_by") or {}).get("total") or 0)
        except (TypeError, ValueError):
            cited = 0
        out.append(
            {
                "source": "google_scholar",
                "title": title,
                "authors": authors,
                "year": year,
                "venue": venue,
                "abstract": re.sub(r"\s+", " ", str(it.get("snippet") or "")).strip()[:600],
                "doi": doi,
                "url": link or (f"https://doi.org/{doi}" if doi else ""),
                "cited_by_count": cited,
            }
        )
    return out


SOURCES = {
    "crossref": search_crossref,
    "semantic_scholar": search_semantic_scholar,
    "openalex": search_openalex,
    "arxiv": search_arxiv,
    "google_scholar": search_google_scholar,  # only yields results when a serpapi key is stored
}


def search(
    query: str,
    *,
    limit: int = 8,
    sources: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    keys: dict | None = None,
) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 3:
        return []
    srcs = [s for s in (sources or list(SOURCES)) if s in SOURCES]
    if not (keys or {}).get("serpapi"):
        srcs = [s for s in srcs if s != "google_scholar"]
    per = max(3, min(limit, 20))
    merged: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(srcs) or 1) as ex:
        futs = {s: ex.submit(SOURCES[s], q, per, year_from, year_to, keys or {}) for s in srcs}
        for s, f in futs.items():
            try:
                items = f.result(timeout=TIMEOUT + 5)
            except Exception:  # noqa: BLE001
                items = []
            for it in items:
                k = _key(it)
                if k in merged:
                    # keep the richer record; merge citation counts
                    cur = merged[k]
                    if len(it.get("abstract") or "") > len(cur.get("abstract") or ""):
                        cur["abstract"] = it["abstract"]
                    cur["cited_by_count"] = max(cur.get("cited_by_count", 0), it.get("cited_by_count", 0))
                    cur.setdefault("sources", []).append(s)
                else:
                    it["sources"] = [s]
                    merged[k] = it
    ranked = sorted(merged.values(), key=lambda x: _score(x, q), reverse=True)[:limit]
    for i, it in enumerate(ranked, 1):
        it["n"] = i
        it["author"] = _authors_str(it.get("authors") or [])
        it["score"] = _score(it, q)
    return ranked


def evidence_block(items: list[dict], query: str = "") -> str:
    """Numbered evidence list for the session context. Seats are told to cite [n]."""
    if not items:
        return ""
    lines = [
        '<evidence note="Scholarly sources retrieved for this session. Cite as [n]. Abstracts are the authors\' claims, not verified facts.">'
    ]
    if query:
        lines.append(f"Query: {query}")
    for it in items:
        ref = f"doi:{it['doi']}" if it.get("doi") else it.get("url", "")
        head = f"[{it['n']}] {it['author']} ({it.get('year') or 'n.d.'}). {it['title']}. {it.get('venue') or ''} {ref}".strip()
        lines.append(head)
        if it.get("abstract"):
            lines.append(f"    {it['abstract']}")
    lines.append("</evidence>")
    return "\n".join(lines)
