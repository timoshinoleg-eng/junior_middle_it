"""Outbound HTTP SSRF guard (v7 Stage 3, B21).

URL preflight historically followed redirects blindly (`allow_redirects=True`)
with no allowlist and no private/loopback IP checks, so a job URL could point
the bot at internal services (e.g. cloud metadata endpoints) — including after
a redirect hop. This module enforces:

1. domain allowlist (job/ATS domains; extend via URL_PREFLIGHT_ALLOWED_HOSTS);
2. DNS resolution checks BEFORE the request — every resolved IP must be a
   public, unicast address (private/loopback/link-local/reserved are blocked);
3. manual redirect following with the SAME validation applied to every hop;
4. hosts outside the allowlist are skipped (status None -> "unknown"), which
   keeps publication safe: only explicit 404/410 ever excludes a vacancy.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import requests

# Default allowlist: well-known public job boards / ATS hosts seen in the
# configured sources. Matched by exact host or "."-suffix.
DEFAULT_ALLOWED_HOST_SUFFIXES = (
    'greenhouse.io',
    'lever.co',
    'ashbyhq.com',
    'hh.ru',
    'habr.com',
    'remoteok.com',
    'weworkremotely.com',
    'themuse.com',
    'findwork.dev',
    'usajobs.gov',
    'adzuna.com', 'adzuna.co.uk', 'adzuna.de', 'adzuna.fr', 'adzuna.es',
    'superjob.ru',
    'reed.co.uk',
    'jooble.org',
    'linkedin.com',
    'wellfound.com', 'angel.co',
    'workable.com',
    'smartrecruiters.com',
    'icims.com',
    'myworkdayjobs.com',
    'taleo.net',
    'ycombinator.com',
    'stripe.com',
    'jobs.ashbyhq.com',
    'builtin.com',
    'stackoverflow.com',
    'github.com',
    'remotive.com',
    'himalayas.app',
    'workingnomads.com',
    'jobspresso.co',
    'wttj.tech',
    'welcometothejungle.com',
    'otcjobs.com',
    'career.yandex.ru',
    'vk.com',
)


def extra_allowed_suffixes() -> List[str]:
    raw = os.getenv('URL_PREFLIGHT_ALLOWED_HOSTS', '')
    return [h.strip().lower() for h in raw.split(',') if h.strip()]


def _host_allowed(host: str) -> bool:
    host = (host or '').lower().rstrip('.')
    if not host:
        return False
    for suffix in DEFAULT_ALLOWED_HOST_SUFFIXES + tuple(extra_allowed_suffixes()):
        s = suffix.lower().rstrip('.')
        if host == s or host.endswith('.' + s):
            return True
    return False


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable -> block
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve_public_ips(host: str, port: Optional[int] = None) -> List[str]:
    """Resolve host; returns [] when unresolvable or ANY IP is non-public."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError):
        return []
    ips = []
    for info in infos:
        ip_str = info[4][0]
        if _is_blocked_ip(ip_str):
            return []  # one hostile record poisons the whole name
        ips.append(ip_str)
    return ips


def validate_url(url: str) -> bool:
    """Scheme + allowlist + resolved-IP validation for one URL."""
    try:
        parsed = urlsplit(str(url or '').strip())
    except (TypeError, ValueError):
        return False
    if parsed.scheme not in ('http', 'https'):
        return False
    host = parsed.hostname or ''
    if not _host_allowed(host):
        return False
    return bool(resolve_public_ips(host, parsed.port))


def guarded_request(method: str, url: str, *, timeout: float,
                    max_redirects: int = 5, **kwargs) -> Optional[requests.Response]:
    """requests wrapper that validates every hop (B21).

    Redirects are followed manually; each Location target passes the same
    allowlist + resolved-IP validation BEFORE any request is issued.
    Returns the final response or None when blocked/unresolvable.
    """
    kwargs.pop('allow_redirects', None)
    current = url
    for _hop in range(max_redirects + 1):
        if not validate_url(current):
            return None
        try:
            response = requests.request(
                method, current, allow_redirects=False, timeout=timeout, **kwargs
            )
        except requests.RequestException:
            return None
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get('Location')
            response.close()
            if not location:
                return None
            current = urljoin(current, location)
            continue
        return response
    return None  # redirect loop


def is_checkable(url: str) -> bool:
    """Cheap pre-check used before spending a request slot."""
    return validate_url(url)
