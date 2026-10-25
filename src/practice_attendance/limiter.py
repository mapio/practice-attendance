"""A sliding-window limit per address, for the requests that name an unknown
token (docs/contract.md, *Tokens*): guessing becomes impractical and a visitor
with the right link is never counted."""

from collections import deque
from ipaddress import IPv6Address, ip_network


def _key(address):
    """The address a visitor is counted under: an IPv6 visitor holds a whole /64,
    so counting single addresses would give her 2**64 of them."""
    try:
        parsed = IPv6Address(address)
    except ValueError:
        return address  # IPv4, or the "?" of an unknown client
    if parsed.ipv4_mapped is not None:
        return str(parsed.ipv4_mapped)
    return str(ip_network(f"{parsed}/64", strict=False))


class Limiter:
    def __init__(self, limit, window, clock):
        self._limit = limit
        self._window = window  # seconds
        self._clock = clock  # seconds, any origin, never going back
        self._hits = {}  # address -> the times of its recent counted requests

    def allow(self, address):
        """True and the request is counted, or False once the address has used up
        its limit in the window; a refused request is not counted."""
        now = self._clock()
        hits = self._hits.setdefault(_key(address), deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        if len(self._hits) > 1000:  # a scan from many addresses must not grow us
            for gone in [
                a for a, h in self._hits.items() if not h or now - h[-1] >= self._window
            ]:
                del self._hits[gone]
        return True
