"""Append-only журнал с хеш-цепочкой.

Формат: одна JSON-строка на событие в файле journal/YYYY-MM.jsonl.
Каждая запись содержит prev (хеш предыдущей) и hash (хеш самой записи),
поэтому любое изменение или удаление строки в середине ломает цепочку
и обнаруживается командой verify.

Журнал — источник истины. Всё остальное (саммари, семантическая память,
векторные индексы) — производные данные, которые можно пересобрать.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from typing import Any, Callable, Dict, Iterator, Optional

JOURNAL_VERSION = 1
STATE_NAME = ".state.json"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(ts_ms: Optional[int] = None) -> str:
    """ULID: сортируемые по времени идентификаторы (10 символов времени + 16 случайных)."""
    ts = int(ts_ms if ts_ms is not None else time.time() * 1000)
    text = ""
    value = ts
    for _ in range(10):
        text = _CROCKFORD[value & 31] + text
        value >>= 5
    rand = int.from_bytes(os.urandom(10), "big")
    tail = ""
    for _ in range(16):
        tail = _CROCKFORD[rand & 31] + tail
        rand >>= 5
    return text + tail


def canonical_json(obj: Any) -> bytes:
    """Детерминированная сериализация: один и тот же объект всегда даёт один хеш."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now_iso(ts: Optional[float] = None) -> str:
    t = time.gmtime(ts if ts is not None else time.time())
    ms = int(((ts if ts is not None else time.time()) % 1) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", t) + ".%03dZ" % ms


class JournalError(Exception):
    pass


class Journal:
    """Append-only журнал. indexer — необязательный колбэк для записи в SQLite-индекс."""

    def __init__(
        self,
        root: str,
        session: str = "s-default",
        project: str = "default",
        indexer: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.root = os.path.abspath(os.path.expanduser(root))
        os.makedirs(self.root, exist_ok=True)
        self.session = session
        self.project = project
        self.indexer = indexer
        self._state_path = os.path.join(self.root, STATE_NAME)

    # ---------- состояние ----------

    def _load_state(self) -> Dict[str, Any]:
        if not os.path.exists(self._state_path):
            return {"seq": 0, "last_hash": "", "updated": None, "version": JOURNAL_VERSION}
        with open(self._state_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save_state(self, state: Dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".state-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, sort_keys=True, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._state_path)

    def last_hash(self) -> str:
        return self._load_state().get("last_hash", "")

    # ---------- запись ----------

    def _path_for(self, ts_iso: str) -> str:
        return os.path.join(self.root, ts_iso[:7] + ".jsonl")

    def append(
        self,
        type: str,
        payload: Dict[str, Any],
        actor: str = "human",
        session: Optional[str] = None,
        project: Optional[str] = None,
        refs: Optional[Dict[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not type or not isinstance(type, str):
            raise JournalError("type обязателен")
        state = self._load_state()
        ts_iso = utc_now_iso(ts)
        record: Dict[str, Any] = {
            "v": JOURNAL_VERSION,
            "id": ulid(int(time.time() * 1000) if ts is None else int(ts * 1000)),
            "ts": ts_iso,
            "session": session or self.session,
            "project": project or self.project,
            "actor": actor,
            "type": type,
            "refs": refs or {},
            "payload": payload,
            "prev": state.get("last_hash", ""),
        }
        record["hash"] = sha256_hex(canonical_json(record))
        self._append_line(self._path_for(ts_iso), record)
        state["seq"] = int(state.get("seq", 0)) + 1
        state["last_hash"] = record["hash"]
        state["updated"] = ts_iso
        self._save_state(state)
        if self.indexer:
            try:
                self.indexer(record)
            except Exception:  # индекс — вторичен, журнал уже записан
                pass
        return record

    def _append_line(self, path: str, record: Dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ---------- чтение ----------

    def iter_records(self) -> Iterator[Dict[str, Any]]:
        files = sorted(f for f in os.listdir(self.root) if f.endswith(".jsonl"))
        for name in files:
            path = os.path.join(self.root, name)
            with open(path, "r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise JournalError("%s:%d повреждённая строка: %s" % (name, lineno, exc))

    def search(self, query: str, limit: int = 20, type: Optional[str] = None) -> list:
        needle = query.lower()
        out = []
        for rec in self.iter_records():
            if type and rec.get("type") != type:
                continue
            blob = json.dumps(rec, ensure_ascii=False).lower()
            if needle in blob:
                out.append(rec)
        return out[-limit:]

    def tail(self, limit: int = 20) -> list:
        return list(self.iter_records())[-limit:]

    # ---------- проверка целостности ----------

    def verify(self) -> Dict[str, Any]:
        prev = ""
        count = 0
        for rec in self.iter_records():
            count += 1
            stored = rec.get("hash", "")
            payload = {k: v for k, v in rec.items() if k != "hash"}
            if sha256_hex(canonical_json(payload)) != stored:
                return {"ok": False, "records": count, "error": "hash mismatch", "id": rec.get("id")}
            if rec.get("prev", "") != prev:
                return {"ok": False, "records": count, "error": "chain broken", "id": rec.get("id")}
            prev = stored
        state = self._load_state()
        return {
            "ok": state.get("last_hash", "") == prev,
            "records": count,
            "error": None if state.get("last_hash", "") == prev else "state mismatch",
            "last_hash": prev,
        }

    def manifest(self) -> Dict[str, str]:
        """sha256 каждого файла журнала — для внешней фиксации (git tag, Zenodo, подпись)."""
        out = {}
        for name in sorted(f for f in os.listdir(self.root) if f.endswith(".jsonl")):
            path = os.path.join(self.root, name)
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            out[name] = h.hexdigest()
        return out
