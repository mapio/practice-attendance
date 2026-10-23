"""A sliding-window limit per address, for the requests that name an unknown
token (docs/contract.md, *Tokens*): guessing becomes impractical and a visitor
with the right link is never counted."""

from collections import deque


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
        hits = self._hits.setdefault(address, deque())
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
