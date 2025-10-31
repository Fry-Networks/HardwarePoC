from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from external_api import ExternalApiClient, ApiError


class _UpdateResult:
    def __init__(self, matched: int, upserted: Optional[str] = None):
        self.matched_count = matched
        self.modified_count = matched
        self.upserted_id = upserted


class _ReplaceResult:
    def __init__(self, acknowledged: bool = True):
        self.acknowledged = acknowledged


class MongoProxyAdmin:
    def command(self, cmd: str) -> Dict[str, Any]:
        if cmd == "ping":
            return {"ok": 1}
        raise ApiError(f"Unsupported admin command: {cmd}")


class MongoProxyClient:
    def __init__(self, api: ExternalApiClient):
        self._api = api
        self.admin = MongoProxyAdmin()

    def __getitem__(self, name: str) -> "MongoProxyDatabase":
        return MongoProxyDatabase(self._api, name)

    def close(self) -> None:
        return None


class MongoProxyDatabase:
    def __init__(self, api: ExternalApiClient, name: str):
        self._api = api
        self._name = name

    def __getitem__(self, name: str) -> "MongoProxyCollection":
        return MongoProxyCollection(self._api, self._name, name)


class MongoProxyCollection:
    def __init__(self, api: ExternalApiClient, db_name: str, coll_name: str):

        self._api = api

        self._db = db_name

        self._coll = coll_name



    def _convert_datetimes(self, value: Any) -> Any:

        if isinstance(value, dt.datetime):

            return value.isoformat()

        if isinstance(value, dict):

            return {k: self._convert_datetimes(v) for k, v in value.items()}

        if isinstance(value, list):

            return [self._convert_datetimes(v) for v in value]

        return value



    # --- Helpers -----------------------------------------------------
    def _coerce_seconds(self, expires: Any, reference: Any) -> int:
        try:
            if isinstance(expires, dt.datetime) and isinstance(reference, dt.datetime):
                delta = expires - reference
                return max(60, int(delta.total_seconds()))
        except Exception:
            pass
        return 900

    def _lease_filter_install_id(self, filter: Dict[str, Any]) -> Optional[str]:
        try:
            clause = filter.get("lease_install_id")
            if isinstance(clause, dict):
                ne = clause.get("$ne")
                if isinstance(ne, str) and ne.strip():
                    return ne.strip()
            if isinstance(clause, str) and clause.strip():
                return clause.strip()
        except Exception:
            pass
        return None

    def _lease_filter_miner_key(self, filter: Dict[str, Any]) -> Optional[str]:
        lease_id = filter.get("_id") if isinstance(filter, dict) else None
        if isinstance(lease_id, str) and lease_id.startswith("lease:"):
            return lease_id.split(":", 1)[1]
        return None

    # --- CRUD shims --------------------------------------------------
    def find_one(self, filter: Optional[Dict[str, Any]] = None, projection: Optional[Dict[str, Any]] = None, sort: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        filter = filter or {}
        if self._db == "PoC" and self._coll == "versions":
            miner_code = filter.get("miner_code") if isinstance(filter, dict) else None
            if not isinstance(miner_code, str):
                return None
            try:
                version = self._api.get_required_version(miner_code)
            except ApiError:
                return None
            if version:
                return {"software_version_needed": version}
            return None
        if self._db == "main" and self._coll == "devices":
            miner_key = filter.get("miner_key") if isinstance(filter, dict) else None
            if not isinstance(miner_key, str):
                return None
            try:
                profile = self._api.get_miner_profile(miner_key)
            except ApiError:
                return None
            if profile.get("exists"):
                doc: Dict[str, Any] = {"_id": miner_key}
                # Try multiple field name variants and nested paths
                hex_id = profile.get("hex_id") or profile.get("hexID") or profile.get("hexId")
                if not hex_id and isinstance(profile.get("position"), dict):
                    hex_id = profile["position"].get("hexId") or profile["position"].get("hex_id") or profile["position"].get("hexID")
                if isinstance(hex_id, str) and hex_id:
                    doc["hexId"] = hex_id
                return doc
            return None
        if self._db == "PoC" and self._coll == "hardware":
            miner_key = filter.get("miner_key") if isinstance(filter, dict) else None
            if not isinstance(miner_key, str):
                return None
            try:
                doc = self._api.get_hardware_doc(miner_key)
            except ApiError:
                return None
            return doc if isinstance(doc, dict) else None
        if self._db == "creds" and self._coll == "hardware":
            miner_key = filter.get("miner_key") if isinstance(filter, dict) else None
            if not isinstance(miner_key, str):
                return None
            try:
                profile = self._api.get_miner_profile(miner_key)
            except ApiError:
                return None
            # Try multiple field name variants and nested paths
            miner_mac = profile.get("registered_mac") or profile.get("miner_mac")
            if not miner_mac and isinstance(profile.get("credentials"), dict):
                miner_mac = profile["credentials"].get("mac_address") or profile["credentials"].get("miner_mac") or profile["credentials"].get("registered_mac")
            if isinstance(miner_mac, str) and miner_mac:
                return {"miner_mac": miner_mac}
            return None
        if self._db == "PoC" and self._coll == "installations":
            miner_key = self._lease_filter_miner_key(filter)
            if not miner_key:
                return None
            exclude_id = self._lease_filter_install_id(filter)
            try:
                status = self._api.lease_status(miner_key)
            except ApiError:
                return None
            holder = status.get("holder_install_id") if isinstance(status, dict) else None
            active = bool(status.get("active")) if isinstance(status, dict) else False
            ttl = status.get("ttl_seconds") if isinstance(status, dict) else None
            if active and isinstance(holder, str) and holder:
                if exclude_id and holder == exclude_id:
                    return None
                if isinstance(ttl, (int, float)) and ttl <= 0:
                    return None
                return {"_id": filter.get("_id")}
            return None
        return None

    def update_one(self, filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        if self._db == "main" and self._coll == "installations":
            miner_key = filter.get("miner_key")
            install_id = filter.get("install_id")
            if not isinstance(miner_key, str) or not isinstance(install_id, str):
                return _UpdateResult(0)
            payload = dict(update.get("$set", {}))

            payload.update(update.get("$setOnInsert", {}))

            payload = self._convert_datetimes(payload)
            try:
                self._api.upsert_installation(miner_key, install_id, payload)
                return _UpdateResult(1)
            except ApiError:
                return _UpdateResult(0)
        if self._db == "PoC" and self._coll == "installations":
            miner_key = self._lease_filter_miner_key(filter)
            update_set = update.get("$set", {}) if isinstance(update, dict) else {}
            install_id = update_set.get("lease_install_id") or update_set.get("install_id")
            if not isinstance(miner_key, str) or not isinstance(install_id, str):
                return _UpdateResult(0)
            lease_seconds = self._coerce_seconds(update_set.get("lease_expires_at"), update_set.get("last_seen_at"))
            try:
                if "$or" in filter:
                    ok = self._api.acquire_installation_lease(miner_key, install_id, lease_seconds)
                else:
                    ok = self._api.renew_installation_lease(miner_key, install_id, lease_seconds)
                return _UpdateResult(1 if ok else 0)
            except ApiError:
                return _UpdateResult(0)
        raise ApiError(f"Unsupported update on {self._db}.{self._coll}")

    def replace_one(self, filter: Dict[str, Any], replacement: Dict[str, Any], upsert: bool = False):
        if self._db == "PoC" and self._coll == "hardware":
            miner_key = filter.get("miner_key")
            if not isinstance(miner_key, str):
                return _ReplaceResult(False)
            try:
                self._api.put_hardware_doc(miner_key, self._convert_datetimes(replacement))
                return _ReplaceResult()
            except ApiError:
                return _ReplaceResult(False)
        raise ApiError(f"Unsupported replace on {self._db}.{self._coll}")

    def delete_many(self, filter: Dict[str, Any]):
        # No-op: the API prunes legacy documents server-side.
        return _UpdateResult(0)

    def find(self, filter: Optional[Dict[str, Any]] = None, projection: Optional[Dict[str, Any]] = None, sort: Optional[Any] = None):
        if self._db == "PoC" and self._coll == "installations":
            miner_key = None
            if isinstance(filter, dict):
                miner_key = filter.get("miner_key")
                if isinstance(miner_key, dict):
                    miner_key = miner_key.get("$eq")
            if not isinstance(miner_key, str):
                return []
            try:
                # lease_history endpoint was removed; handle gracefully.
                leases = self._api.lease_history(miner_key)
            except (ApiError, AttributeError):
                return []
            return leases
        raise ApiError(f"Unsupported find on {self._db}.{self._coll}")


__all__ = [
    "MongoProxyClient",
]
