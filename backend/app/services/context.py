"""Context ingestion: URL fetch (SSRF-guarded), uploaded text files, and per-seat truncation.

URL fetching runs inside the container, which also holds provider keys, so it must not be usable
as a proxy into the host, the Docker network, or cloud metadata endpoints. Rules:
  * http/https only; no credentials in the URL; no non-default ports unless allowed
  * every hostname is resolved and EVERY resolved address must be public (not loopback, private,
    link-local, multicast, reserved, or the IPv4-mapped forms of those)
  * redirects are followed manually and each hop re-checked
  * response capped at MAX_BYTES and TIMEOUT; only text-like content types are accepted
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx

MAX_BYTES = 1_500_000
TIMEOUT = 15.0
MAX_REDIRECTS = 4
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/javascript", "application/x-yaml")
ALLOWED_PORTS = {80, 443}


class UnsafeURL(ValueError):
    pass


def _is_public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    blocked = a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified
    return a.is_global and not blocked


def _check(url: str) -> tuple[str, str]:
    """Validate and resolve. Returns (host, pinned_ip)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise UnsafeURL("Only http and https URLs are allowed")
    if u.username or u.password:
        raise UnsafeURL("Credentials in URLs are not allowed")
    host = (u.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeURL("URL has no host")
    port = u.port or (443 if u.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise UnsafeURL(f"Port {port} is not allowed")
    if host in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
        raise UnsafeURL("Local hostnames are not allowed")
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURL(f"Cannot resolve {host}") from exc
    ips = {i[4][0] for i in infos}
    if not ips or not all(_is_public(ip) for ip in ips):
        raise UnsafeURL(f"{host} resolves to a non-public address")
    return host, sorted(ips)[0]


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n\s*\n+", "\n\n", raw)
    return raw.strip()


def fetch_url(url: str) -> dict:
    """Fetch a public URL as text. Raises UnsafeURL or httpx.HTTPError.

    The connection is pinned to the IP that passed the check (Host header + SNI carry the real name),
    so a DNS answer that changes between our check and the connect cannot redirect us inward.
    """
    current = url.strip()
    ua = "TheHive/0.2 (+https://github.com/SyphenAI/TheHive)"
    for _ in range(MAX_REDIRECTS + 1):
        host, ip = _check(current)
        u = httpx.URL(current)
        pinned = u.copy_with(host=ip if ":" not in ip else f"[{ip}]")
        headers = {
            "User-Agent": ua,
            "Host": u.netloc.decode(),
            "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.5",
        }
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers=headers) as c:
            req = c.build_request("GET", pinned, extensions={"sni_hostname": host})
            r = c.send(req, stream=True)
            try:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = str(u.join(r.headers["location"]))
                    continue
                r.raise_for_status()
                ctype = (r.headers.get("content-type") or "").lower()
                if not any(ctype.startswith(t) for t in TEXT_TYPES):
                    raise UnsafeURL(f"Unsupported content type: {ctype.split(';')[0] or 'unknown'}")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BYTES:
                        raise UnsafeURL(f"Response larger than {MAX_BYTES // 1000} KB")
                text = buf.decode(r.encoding or "utf-8", errors="replace")
            finally:
                r.close()
        title = ""
        if "html" in ctype:
            m = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
            title = html.unescape(m.group(1)).strip() if m else ""
            text = _html_to_text(text)
        return {
            "url": current,
            "host": host,
            "content_type": ctype.split(";")[0],
            "title": title,
            "chars": len(text),
            "text": text,
        }
    raise UnsafeURL("Too many redirects")


def source_block(label: str, text: str, kind: str = "document") -> str:
    """Wrap an ingested source so prompts treat it as data."""
    return f'<source kind="{kind}" label="{html.escape(label, quote=True)}">\n{text.strip()}\n</source>'


# ---------- truncation ----------
def fit_context(context: str, *, model_context_tokens: int, reserve_tokens: int) -> tuple[str, bool]:
    """Trim context so prompt + outputs fit the seat's window. ~4 chars/token. Returns (text, truncated)."""
    budget_chars = max(2000, (model_context_tokens - reserve_tokens) * 4)
    if len(context) <= budget_chars:
        return context, False
    head = context[: int(budget_chars * 0.8)]
    tail = context[-int(budget_chars * 0.15) :]
    note = f"\n\n[… context truncated for this seat: {len(context) - len(head) - len(tail)} characters omitted …]\n\n"
    return head + note + tail, True
