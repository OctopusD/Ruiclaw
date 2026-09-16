"""Small, local working-memory index for relevant context retrieval."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import uuid4

from ruiclaw.agent.hook import AgentHook, AgentHookContext
from ruiclaw.providers.base import ToolCallRequest

WORKING_MEMORY_META = "ruiclaw_working_memory"
_MAX_ENTRIES = 100
_MAX_SUMMARY_CHARS = 500
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[\u4e00-\u9fff]")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*([^\s,;]+)"
)
_STORE_LOCK = threading.Lock()


class MemorySource(TypedDict):
    type: str
    path: str
    content_hash: str


class MemoryEntry(TypedDict):
    memory_id: str
    kind: str
    content: str
    tags: list[str]
    source: MemorySource
    created_at: str
    last_accessed_at: str


class RetrievedMemory(TypedDict):
    memory_id: str
    content: str
    score: int
    reasons: list[str]
    source: str


@dataclass(frozen=True, slots=True)
class MemoryRetrieval:
    candidate_count: int
    stale_rejected_count: int
    selected: tuple[RetrievedMemory, ...]

    def metadata(self) -> dict[str, object]:
        return {
            "candidate_count": self.candidate_count,
            "selected_count": len(self.selected),
            "stale_rejected_count": self.stale_rejected_count,
            "selected": [
                {
                    "memory_id": item["memory_id"],
                    "score": item["score"],
                    "reasons": list(item["reasons"]),
                    "source": item["source"],
                }
                for item in self.selected
            ],
        }

    def render(self) -> str:
        if not self.selected:
            return ""
        lines = ["# Relevant Working Memory"]
        lines.extend(f"- {item['content']}" for item in self.selected)
        return "\n".join(lines)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _tokens(value: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN_RE.finditer(value)}


def _file_hash(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class WorkingMemoryStore:
    """Persist and retrieve bounded file summaries under ``.ruiclaw/memory``."""

    def __init__(self, workspace: Path, *, max_entries: int = _MAX_ENTRIES) -> None:
        self.workspace = workspace.expanduser().resolve(strict=False)
        self.max_entries = max_entries
        self.memory_dir = self.workspace / ".ruiclaw" / "memory"
        self.index_path = self.memory_dir / "index.json"
        self.entries_path = self.memory_dir / "entries.jsonl"

    def remember_file(self, path: Path, summary: str) -> MemoryEntry | None:
        resolved = path.expanduser().resolve(strict=False)
        relative = self._relative_path(resolved)
        content_hash = _file_hash(resolved)
        cleaned = _sanitize_summary(summary)
        if relative is None or content_hash is None or not cleaned:
            return None

        with _STORE_LOCK:
            entries = self._read_entries()
            existing = next(
                (entry for entry in entries if entry["source"]["path"] == relative),
                None,
            )
            now = _timestamp()
            entry: MemoryEntry = {
                "memory_id": (
                    existing["memory_id"] if existing else "mem_" + uuid4().hex[:12]
                ),
                "kind": "file_summary",
                "content": cleaned,
                "tags": _path_tags(relative),
                "source": {
                    "type": "file",
                    "path": relative,
                    "content_hash": content_hash,
                },
                "created_at": existing["created_at"] if existing else now,
                "last_accessed_at": now,
            }
            current = [item for item in entries if item["memory_id"] != entry["memory_id"]]
            current.append(entry)
            current = current[-self.max_entries:]
            self._write_index(current)
            self._append_entry(entry)
        return entry

    def retrieve(self, query: str, *, limit: int = 3) -> MemoryRetrieval:
        query_tokens = _tokens(query)
        entries = self._read_entries()
        ranked: list[tuple[int, str, RetrievedMemory]] = []
        stale = 0
        for entry in entries:
            source = entry["source"]
            tag_overlap = query_tokens & {tag.lower() for tag in entry["tags"]}
            content_overlap = query_tokens & _tokens(entry["content"])
            path_overlap = query_tokens & _tokens(source["path"])
            reasons: list[str] = []
            score = 0
            if path_overlap:
                score += 5 * len(path_overlap)
                reasons.append("path_match")
            if tag_overlap:
                score += 3 * len(tag_overlap)
                reasons.append("tag_match")
            if content_overlap:
                score += len(content_overlap)
                reasons.append("keyword_overlap")
            if score <= 0:
                continue
            path = self.workspace / source["path"]
            if source["type"] == "file" and _file_hash(path) != source["content_hash"]:
                stale += 1
                continue
            ranked.append((score, entry["last_accessed_at"], {
                "memory_id": entry["memory_id"],
                "content": entry["content"],
                "score": score,
                "reasons": reasons,
                "source": source["path"],
            }))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]["memory_id"]), reverse=True)
        selected = tuple(item[2] for item in ranked[:max(0, limit)])
        return MemoryRetrieval(
            candidate_count=len(entries),
            stale_rejected_count=stale,
            selected=selected,
        )

    def _relative_path(self, path: Path) -> str | None:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return None

    def _read_entries(self) -> list[MemoryEntry]:
        try:
            value: object = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []
        if not isinstance(value, dict):
            return []
        raw_entries = cast(dict[str, object], value).get("entries")
        if not isinstance(raw_entries, list):
            return []
        entries: list[MemoryEntry] = []
        for raw in cast(list[object], raw_entries):
            parsed = _parse_entry(raw)
            if parsed is not None:
                entries.append(parsed)
        return entries[-self.max_entries:]

    def _write_index(self, entries: list[MemoryEntry]) -> None:
        _atomic_write_json(self.index_path, {
            "schema_version": 1,
            "entries": cast(list[object], entries),
        })

    def _append_entry(self, entry: MemoryEntry) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        with open(self.entries_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())


class WorkingMemoryHook(AgentHook):
    """Capture successful workspace file reads as short, freshness-bound notes."""

    def __init__(self, workspace: Path) -> None:
        super().__init__()
        self.store = WorkingMemoryStore(workspace)

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        del context, tool
        if tool_call.name != "read_file" or not isinstance(params, dict):
            return
        path_value = cast(dict[object, object], params).get("path")
        if not isinstance(path_value, str) or not isinstance(result, str):
            return
        if result.startswith(("Error", "[File unchanged since last read:")):
            return
        path = Path(path_value)
        if not path.is_absolute():
            path = self.store.workspace / path
        self.store.remember_file(path, result)


def _path_tags(path: str) -> list[str]:
    tokens = _tokens(path)
    return sorted(token for token in tokens if len(token) > 1)[:12]


def _sanitize_summary(content: str) -> str:
    lines: list[str] = []
    for raw in content.splitlines():
        line = re.sub(r"^\s*\d+[|:]\s?", "", raw).strip()
        if not line:
            continue
        lines.append(_SECRET_RE.sub(r"\1=[REDACTED]", line))
        if len(lines) == 3:
            break
    summary = " | ".join(lines)
    return summary[:_MAX_SUMMARY_CHARS].strip()


def _parse_entry(value: object) -> MemoryEntry | None:
    if not isinstance(value, dict):
        return None
    raw = cast(dict[object, object], value)
    source_value = raw.get("source")
    tags_value = raw.get("tags")
    required = ("memory_id", "kind", "content", "created_at", "last_accessed_at")
    if not all(isinstance(raw.get(key), str) for key in required):
        return None
    if not isinstance(source_value, dict) or not isinstance(tags_value, list):
        return None
    source_raw = cast(dict[object, object], source_value)
    raw_tags = cast(list[object], tags_value)
    if not all(isinstance(source_raw.get(key), str) for key in ("type", "path", "content_hash")):
        return None
    if not all(isinstance(tag, str) for tag in raw_tags):
        return None
    return cast(MemoryEntry, {
        "memory_id": raw["memory_id"],
        "kind": raw["kind"],
        "content": raw["content"],
        "tags": [tag for tag in raw_tags if isinstance(tag, str)],
        "source": {
            "type": source_raw["type"],
            "path": source_raw["path"],
            "content_hash": source_raw["content_hash"],
        },
        "created_at": raw["created_at"],
        "last_accessed_at": raw["last_accessed_at"],
    })
