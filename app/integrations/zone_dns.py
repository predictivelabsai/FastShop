"""Zone.ee DNS (ZoneID API v2): point a merchant's domain at the store in one step.

The merchant saves their ZoneID username and an API key (Account settings -> Security ->
API keys). FastShop then works out exactly which records to change for the hostnames being
connected, shows that plan, and applies it only when the merchant confirms. Only the A, AAAA
and CNAME records of those hostnames, and FastShop's own TXT proof records, are ever
touched: mail (MX, SPF) and every other name stay as they are. Each applied change keeps
the previous values so it can be undone.

API: https://api.zone.eu/v2 (Basic auth username:api_key). Records are addressed by full
hostname ("example.com", "www.example.com") and identified by their numeric id.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import httpx

from app.services import CommerceError

API = "https://api.zone.eu/v2"
TIMEOUT_SECONDS = 15.0
ROUTING_TYPES = ("a", "aaaa", "cname")


@dataclass(frozen=True)
class Change:
    action: str          # create | update | delete
    type: str            # a | aaaa | cname | txt
    name: str            # full hostname
    old: str = ""        # previous destination (update, delete)
    new: str = ""        # new destination (create, update)
    record_id: str = ""  # Zone's id (update, delete)

    def describe(self) -> str:
        kind = self.type.upper()
        if self.action == "create":
            return f"Add {kind} {self.name} → {self.new}"
        if self.action == "update":
            return f"Change {kind} {self.name} from {self.old} to {self.new}"
        return f"Remove {kind} {self.name} ({self.old})"


class ZoneClient:
    def __init__(self, username: str, api_key: str, *, transport=None):
        self.http = httpx.Client(base_url=API, auth=(username, api_key), timeout=TIMEOUT_SECONDS,
                                 transport=transport, headers={"Accept": "application/json"})

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise CommerceError("Zone couldn't be reached. Try again in a minute.") from exc
        if response.status_code in (401, 403):
            raise CommerceError("Zone didn't accept that username and API key.")
        if response.status_code == 404:
            raise CommerceError("Zone has no DNS zone for this domain in that account.")
        if response.status_code == 429:
            raise CommerceError("Zone is limiting requests right now. Try again in a minute.")
        if response.status_code >= 400:
            detail = response.headers.get("X-Status-Message", "").strip()
            raise CommerceError("Zone refused the change" + (f": {detail}" if detail else "."))
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def zones(self) -> list[str]:
        rows = self._request("GET", "/dns", headers={"x-pager-limit": "100"}) or []
        return [str(row.get("identificator") or row.get("name") or "").lower() for row in rows]

    def has_zone(self, zone: str) -> bool:
        try:
            self._request("GET", f"/dns/{zone}")
        except CommerceError as exc:
            if "no DNS zone" in str(exc):
                return False
            raise
        return True

    def records(self, zone: str, kind: str) -> list[dict]:
        return list(self._request("GET", f"/dns/{zone}/{kind}",
                                  headers={"x-pager-limit": "100"}) or [])

    def create(self, zone: str, kind: str, name: str, destination: str) -> str:
        rows = self._request("POST", f"/dns/{zone}/{kind}",
                             json={"name": name, "destination": destination}) or [{}]
        return str(rows[0].get("id", ""))

    def update(self, zone: str, kind: str, record_id: str, name: str, destination: str) -> None:
        self._request("PUT", f"/dns/{zone}/{kind}/{record_id}",
                      json={"name": name, "destination": destination})

    def delete(self, zone: str, kind: str, record_id: str) -> None:
        self._request("DELETE", f"/dns/{zone}/{kind}/{record_id}")


def _same(a: str, b: str) -> bool:
    return a.strip().rstrip(".").lower() == b.strip().rstrip(".").lower()


def plan(client: ZoneClient, zone: str, wanted: list[tuple[str, str, str]]) -> list[Change]:
    """The changes that make ``wanted`` (type, full hostname, value) true in ``zone``.

    A hostname that should point at the store keeps exactly one routing record: conflicting
    A, AAAA or CNAME records for that same name are replaced or removed. TXT proof records
    are only added, never removed, so other TXT records (SPF, verification) are untouched.
    """
    existing = {kind: client.records(zone, kind) for kind in (*ROUTING_TYPES, "txt")}
    changes: list[Change] = []
    for kind, name, value in wanted:
        kind = kind.lower()
        on_name = {k: [r for r in rows if _same(str(r.get("name", "")), name)]
                   for k, rows in existing.items()}
        if kind == "txt":
            if not any(_same(str(r.get("destination", "")), value) for r in on_name["txt"]):
                changes.append(Change("create", "txt", name, new=value))
            continue
        keep = on_name[kind]
        match = next((r for r in keep if _same(str(r.get("destination", "")), value)), None)
        if match is None and keep:
            first = keep[0]
            changes.append(Change("update", kind, name, old=str(first.get("destination", "")),
                                  new=value, record_id=str(first.get("id", ""))))
            match = first
        elif match is None:
            changes.append(Change("create", kind, name, new=value))
        for record in keep:
            if record is not match:
                changes.append(Change("delete", kind, name, old=str(record.get("destination", "")),
                                      record_id=str(record.get("id", ""))))
        for other in ROUTING_TYPES:
            if other == kind:
                continue
            for record in on_name[other]:
                changes.append(Change("delete", other, name, old=str(record.get("destination", "")),
                                      record_id=str(record.get("id", ""))))
    return changes


def apply(client: ZoneClient, zone: str, changes: list[Change]) -> list[dict]:
    """Apply changes in a safe order and return what is needed to undo them.

    Deletions run first so a CNAME can replace an A record on the same name.
    """
    undo: list[dict] = []
    ordered = ([c for c in changes if c.action == "delete"]
               + [c for c in changes if c.action == "update"]
               + [c for c in changes if c.action == "create"])
    for change in ordered:
        if change.action == "delete":
            client.delete(zone, change.type, change.record_id)
            undo.append(asdict(change))
        elif change.action == "update":
            client.update(zone, change.type, change.record_id, change.name, change.new)
            undo.append(asdict(change))
        else:
            new_id = client.create(zone, change.type, change.name, change.new)
            undo.append({**asdict(change), "record_id": new_id})
    return undo


def undo(client: ZoneClient, zone: str, applied: list[dict]) -> int:
    """Reverse a previous ``apply`` (newest first). Returns how many records were restored."""
    restored = 0
    for item in reversed(applied):
        change = Change(**item)
        if change.action == "create" and change.record_id:
            client.delete(zone, change.type, change.record_id)
        elif change.action == "update":
            client.update(zone, change.type, change.record_id, change.name, change.old)
        elif change.action == "delete":
            client.create(zone, change.type, change.name, change.old)
        restored += 1
    return restored
