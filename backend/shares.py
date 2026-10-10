"""Live walk sharing (Contract 5 in docs/CONTRACTS.md).

A walker creates a share holding the route they're following and gets back a
public id (for the link) and a secret token (for posting updates). Viewers
read the share by id. Everything lives in memory: shares disappear when the
server restarts, TTL_S after their last update, or ENDED_KEEP_S after the
walk ends.
"""
import secrets
import threading
import time
from datetime import datetime, timezone

from routing import RouteError

TTL_S = 12 * 3600
ENDED_KEEP_S = 3600
MAX_SHARES = 1000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ShareStore:
    def __init__(self, ttl_s=TTL_S, ended_keep_s=ENDED_KEEP_S, max_shares=MAX_SHARES, clock=time.time):
        self.ttl_s, self.ended_keep_s, self.max_shares = ttl_s, ended_keep_s, max_shares
        self.clock = clock
        self.shares = {}
        self.lock = threading.Lock()

    def create(self, route, route_name, from_label, to_label):
        now = self.clock()
        with self.lock:
            self._expire(now)
            if len(self.shares) >= self.max_shares:
                raise RouteError(503, "too_many_shares", "Too many walks are being shared right now. Try again later.")
            share_id = secrets.token_urlsafe(9)
            token = secrets.token_urlsafe(24)
            self.shares[share_id] = {
                "token": token,
                "status": "active",
                "route_name": route_name,
                "from_label": from_label,
                "to_label": to_label,
                "route": route,
                "started_at": now,
                "updated_at": now,
                "ended_at": None,
                "location": None,
            }
        return {"id": share_id, "token": token}

    def update(self, share_id, token, coord, accuracy_m, progress_m):
        now = self.clock()
        with self.lock:
            s = self._owned(share_id, token, now)
            if s["status"] != "active":
                raise RouteError(409, "share_ended", "This walk has already ended.")
            length = s["route"]["length_m"]
            s["location"] = {
                "coord": [round(coord[0], 6), round(coord[1], 6)],
                "accuracy_m": None if accuracy_m is None else round(accuracy_m, 1),
                "progress_m": round(min(max(progress_m, 0.0), length), 1),
                "updated_at": iso(now),
            }
            s["updated_at"] = now

    def set_route(self, share_id, token, route, route_name):
        """Swap in a new route (the walker rerouted); progress restarts on it."""
        now = self.clock()
        with self.lock:
            s = self._owned(share_id, token, now)
            if s["status"] != "active":
                raise RouteError(409, "share_ended", "This walk has already ended.")
            s["route"], s["route_name"] = route, route_name
            if s["location"] is not None:
                s["location"]["progress_m"] = 0.0
            s["updated_at"] = now

    def end(self, share_id, token, arrived):
        now = self.clock()
        with self.lock:
            s = self._owned(share_id, token, now)
            if s["status"] == "active":
                s["status"] = "arrived" if arrived else "ended"
                s["ended_at"] = s["updated_at"] = now

    def get(self, share_id):
        now = self.clock()
        with self.lock:
            s = self._find(share_id, now)
            return {
                "id": share_id,
                "status": s["status"],
                "route_name": s["route_name"],
                "from_label": s["from_label"],
                "to_label": s["to_label"],
                "route": s["route"],
                "started_at": iso(s["started_at"]),
                "updated_at": iso(s["updated_at"]),
                "ended_at": None if s["ended_at"] is None else iso(s["ended_at"]),
                "location": s["location"],
            }

    # --- internals (call with the lock held) ---------------------------------

    def _expire(self, now):
        for sid in [sid for sid, s in self.shares.items() if self._expired(s, now)]:
            del self.shares[sid]

    def _expired(self, s, now):
        if s["ended_at"] is not None:
            return now - s["ended_at"] > self.ended_keep_s
        return now - s["updated_at"] > self.ttl_s

    def _find(self, share_id, now):
        s = self.shares.get(share_id)
        if s is None or self._expired(s, now):
            self.shares.pop(share_id, None)
            raise RouteError(404, "share_not_found", "This shared walk doesn't exist or has expired.")
        return s

    def _owned(self, share_id, token, now):
        s = self._find(share_id, now)
        if not secrets.compare_digest(s["token"], token):
            raise RouteError(403, "bad_token", "Only the person walking can update this walk.")
        return s
