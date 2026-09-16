"""Persistent scheduling state for Run-driven Memory and Skill reviews."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import shutil
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

EvolutionScope = Literal["memory", "skills", "combined"]
_INTERNAL_RUN_KINDS = frozenset({"dream", "evolution", "memory_review", "skill_review"})


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class EvolutionSettings:
    """Runtime thresholds copied from validated application configuration."""

    enabled: bool = True
    memory_enabled: bool = True
    memory_valid_turns: int = 10
    skills_enabled: bool = True
    skill_tool_iterations: int = 20
    skill_minimum_candidate_runs: int = 2


@dataclass(frozen=True, slots=True)
class EvolutionObservation:
    """Content-free learning evidence emitted by one completed user Run."""

    run_id: str
    session_key: str
    turn_id: str
    memory_turns: int = 0
    skill_tool_iterations: int = 0


@dataclass(frozen=True, slots=True)
class EvolutionBatch:
    """Immutable candidate snapshot claimed by one review."""

    review_id: str
    scope: EvolutionScope
    memory_candidates: tuple[EvolutionObservation, ...] = ()
    skill_candidates: tuple[EvolutionObservation, ...] = ()
    trigger: str = "threshold"

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            candidate.run_id
            for candidate in (*self.memory_candidates, *self.skill_candidates)
        ))


class EvolutionFileJournal:
    """Keep file-level review evidence and recoverable pre-review Skill copies."""

    def __init__(self, workspace: Path, batch: EvolutionBatch) -> None:
        self.workspace = workspace
        self.batch = batch
        self.review_dir = workspace / ".ruiclaw" / "evolution" / "reviews" / batch.review_id
        self.manifest_path = self.review_dir / "changes.json"
        self._before: dict[str, dict[str, Any]] = {}

    def capture_before(self) -> None:
        self._before = self._inventory()
        if self.batch.scope in {"skills", "combined"}:
            for relative in self._skill_paths():
                source = self.workspace / relative
                target = self.review_dir / "before" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target, follow_symlinks=False)
        self._write({
            "version": 1,
            "review_id": self.batch.review_id,
            "scope": self.batch.scope,
            "trigger": self.batch.trigger,
            "input_run_ids": list(self.batch.run_ids),
            "status": "running",
            "started_at": _timestamp(),
            "finished_at": None,
            "changed_files": [],
            "before": self._before,
            "after": {},
        })

    def finish(self, *, succeeded: bool) -> tuple[str, ...]:
        after = self._inventory()
        changed = tuple(sorted(
            path
            for path in self._before.keys() | after.keys()
            if self._before.get(path) != after.get(path)
        ))
        self._write({
            "version": 1,
            "review_id": self.batch.review_id,
            "scope": self.batch.scope,
            "trigger": self.batch.trigger,
            "input_run_ids": list(self.batch.run_ids),
            "status": "succeeded" if succeeded else "failed",
            "started_at": self._started_at(),
            "finished_at": _timestamp(),
            "changed_files": list(changed),
            "before": self._before,
            "after": after,
        })
        return changed

    def _inventory(self) -> dict[str, dict[str, Any]]:
        paths: list[Path] = []
        if self.batch.scope in {"memory", "combined"}:
            paths.extend(
                self.workspace / relative
                for relative in ("SOUL.md", "USER.md", "memory/MEMORY.md")
            )
        if self.batch.scope in {"skills", "combined"}:
            paths.extend(self.workspace / relative for relative in self._skill_paths())
        result: dict[str, dict[str, Any]] = {}
        for path in paths:
            if not path.is_file() or path.is_symlink():
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            relative = path.relative_to(self.workspace).as_posix()
            result[relative] = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        return result

    def _skill_paths(self) -> tuple[Path, ...]:
        skills_dir = self.workspace / "skills"
        if not skills_dir.is_dir():
            return ()
        return tuple(
            path.relative_to(self.workspace)
            for path in sorted(skills_dir.rglob("*"))
            if path.is_file() and not path.is_symlink()
        )

    def _started_at(self) -> str:
        try:
            value: object = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return _timestamp()
        if isinstance(value, dict):
            value_data = cast(dict[str, object], value)
            started_at = value_data.get("started_at")
            if isinstance(started_at, str):
                return started_at
        return _timestamp()

    def _write(self, payload: dict[str, Any]) -> None:
        self.review_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.manifest_path.with_name(f".{self.manifest_path.name}.{uuid4().hex}.tmp")
        try:
            with open(tmp_path, "x", encoding="utf-8") as handle:
                os.chmod(tmp_path, 0o600)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.manifest_path)
        finally:
            tmp_path.unlink(missing_ok=True)


class EvolutionStateStore:
    """Serialize candidate accounting and review claims for one workspace."""

    _VERSION = 1

    def __init__(self, workspace: Path, settings: EvolutionSettings) -> None:
        self.settings = settings
        self.state_path = workspace / ".ruiclaw" / "evolution" / "state.json"
        self._lock = asyncio.Lock()
        self._state = self._load()

    async def record(self, observation: EvolutionObservation) -> bool:
        """Record one observation once and return whether an automatic review is due."""
        async with self._lock:
            if not self.settings.enabled:
                return False
            changed = False
            if observation.memory_turns > 0:
                changed |= self._append_unique("memory_review", observation)
            if observation.skill_tool_iterations > 0:
                changed |= self._append_unique("skill_review", observation)
            if changed:
                self._refresh_pending_statuses()
                self._write()
            return self._is_due("memory_review") or self._is_due("skill_review")

    async def claim(
        self,
        scope: EvolutionScope | None = None,
        *,
        force: bool = False,
    ) -> EvolutionBatch | None:
        """Atomically claim due candidates; forced claims still require queued evidence."""
        async with self._lock:
            if not self.settings.enabled or self._state.get("current_review") is not None:
                return None
            memory_due = self._domain_selected("memory_review", scope, force)
            skills_due = self._domain_selected("skill_review", scope, force)
            if not memory_due and not skills_due:
                return None

            resolved_scope: EvolutionScope = (
                "combined" if memory_due and skills_due else "memory" if memory_due else "skills"
            )
            review_id = "evo_" + uuid4().hex[:16]
            memory_candidates = (
                self._domain_candidates("memory_review") if memory_due else ()
            )
            skill_candidates = (
                self._domain_candidates("skill_review") if skills_due else ()
            )
            batch = EvolutionBatch(
                review_id=review_id,
                scope=resolved_scope,
                memory_candidates=memory_candidates,
                skill_candidates=skill_candidates,
                trigger="manual" if force else "threshold",
            )
            self._state["current_review"] = self._batch_payload(batch)
            for domain, selected in (
                ("memory_review", memory_due),
                ("skill_review", skills_due),
            ):
                if selected:
                    cast(dict[str, Any], self._state[domain])["status"] = "reviewing"
            self._write()
            return batch

    async def complete(
        self,
        batch: EvolutionBatch,
        *,
        succeeded: bool,
        error: str | None = None,
    ) -> None:
        """Commit or release a claim without losing candidates added while it ran."""
        async with self._lock:
            current = self._state.get("current_review")
            if not isinstance(current, dict):
                return
            current_data = cast(dict[str, object], current)
            if current_data.get("review_id") != batch.review_id:
                return
            now = _timestamp()
            selected = {
                "memory_review": batch.memory_candidates,
                "skill_review": batch.skill_candidates,
            }
            for domain_name, candidates in selected.items():
                if not candidates:
                    continue
                domain = cast(dict[str, Any], self._state[domain_name])
                if succeeded:
                    claimed_ids = {candidate.run_id for candidate in candidates}
                    remaining = [
                        item
                        for item in cast(list[dict[str, Any]], domain["pending"])
                        if item.get("run_id") not in claimed_ids
                    ]
                    domain["pending"] = remaining
                    domain["last_reviewed_run_id"] = candidates[-1].run_id
                    domain["last_reviewed_at"] = now
                    domain["last_error"] = None
                else:
                    domain["last_error"] = error or "review failed"
            self._state["current_review"] = None
            self._refresh_pending_statuses()
            self._write()

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return cast(dict[str, Any], json.loads(json.dumps(self._state)))

    def _load(self) -> dict[str, Any]:
        state = self._empty_state()
        try:
            value: object = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return state
        if not isinstance(value, dict):
            return state
        loaded = cast(dict[str, object], value)
        if loaded.get("version") != self._VERSION:
            return state
        for name in ("memory_review", "skill_review"):
            domain = loaded.get(name)
            if not isinstance(domain, dict):
                continue
            domain_data = cast(dict[str, object], domain)
            pending = domain_data.get("pending")
            if isinstance(pending, list):
                pending_items = cast(list[object], pending)
                state[name].update({
                    "pending": [
                        cast(dict[str, Any], item)
                        for item in pending_items
                        if isinstance(item, dict)
                        and isinstance(cast(dict[str, object], item).get("run_id"), str)
                    ],
                    "last_reviewed_run_id": domain_data.get("last_reviewed_run_id"),
                    "last_reviewed_at": domain_data.get("last_reviewed_at"),
                    "last_error": domain_data.get("last_error"),
                })
        # A persisted claim has no live owner after process restart. Release it.
        self._state = state
        self._refresh_pending_statuses()
        return state

    @classmethod
    def _empty_state(cls) -> dict[str, Any]:
        def domain() -> dict[str, Any]:
            return {
                "status": "accumulating",
                "pending": [],
                "last_reviewed_run_id": None,
                "last_reviewed_at": None,
                "last_error": None,
            }

        return {
            "version": cls._VERSION,
            "memory_review": domain(),
            "skill_review": domain(),
            "current_review": None,
        }

    def _append_unique(self, domain_name: str, observation: EvolutionObservation) -> bool:
        domain = cast(dict[str, Any], self._state[domain_name])
        pending = cast(list[dict[str, Any]], domain["pending"])
        if any(item.get("run_id") == observation.run_id for item in pending):
            return False
        pending.append(asdict(observation))
        return True

    def _domain_candidates(self, domain_name: str) -> tuple[EvolutionObservation, ...]:
        domain = cast(dict[str, Any], self._state[domain_name])
        candidates: list[EvolutionObservation] = []
        for item in cast(list[dict[str, Any]], domain["pending"]):
            try:
                candidates.append(EvolutionObservation(
                    run_id=str(item["run_id"]),
                    session_key=str(item["session_key"]),
                    turn_id=str(item["turn_id"]),
                    memory_turns=_positive_int(item.get("memory_turns")),
                    skill_tool_iterations=_positive_int(item.get("skill_tool_iterations")),
                ))
            except KeyError:
                continue
        return tuple(candidates)

    def _domain_selected(
        self,
        domain_name: str,
        scope: EvolutionScope | None,
        force: bool,
    ) -> bool:
        expected = "memory" if domain_name == "memory_review" else "skills"
        if scope not in (None, "combined", expected):
            return False
        candidates = self._domain_candidates(domain_name)
        return bool(candidates) and (force or self._is_due(domain_name))

    def _is_due(self, domain_name: str) -> bool:
        candidates = self._domain_candidates(domain_name)
        if domain_name == "memory_review":
            total = sum(candidate.memory_turns for candidate in candidates)
            return self.settings.memory_enabled and total >= self.settings.memory_valid_turns
        total = sum(candidate.skill_tool_iterations for candidate in candidates)
        return (
            self.settings.skills_enabled
            and len(candidates) >= self.settings.skill_minimum_candidate_runs
            and total >= self.settings.skill_tool_iterations
        )

    def _refresh_pending_statuses(self) -> None:
        reviewing = self._state.get("current_review")
        for name in ("memory_review", "skill_review"):
            domain = cast(dict[str, Any], self._state[name])
            if isinstance(reviewing, dict) and domain.get("status") == "reviewing":
                continue
            domain["status"] = "review_pending" if self._is_due(name) else "accumulating"

    @staticmethod
    def _batch_payload(batch: EvolutionBatch) -> dict[str, Any]:
        return {
            "review_id": batch.review_id,
            "scope": batch.scope,
            "trigger": batch.trigger,
            "memory_run_ids": [item.run_id for item in batch.memory_candidates],
            "skill_run_ids": [item.run_id for item in batch.skill_candidates],
            "started_at": _timestamp(),
        }

    def _write(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_name(f".{self.state_path.name}.{uuid4().hex}.tmp")
        try:
            with open(tmp_path, "x", encoding="utf-8") as handle:
                os.chmod(tmp_path, 0o600)
                json.dump(self._state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.state_path)
            with suppress(PermissionError):
                directory_fd = os.open(str(self.state_path.parent), os.O_RDONLY)
                try:
                    try:
                        os.fsync(directory_fd)
                    except OSError as exc:
                        if exc.errno != errno.EINVAL:
                            raise
                finally:
                    os.close(directory_fd)
        finally:
            tmp_path.unlink(missing_ok=True)


class EvolutionCoordinator:
    """Claim batches and run one background reviewer at a time."""

    def __init__(
        self,
        store: EvolutionStateStore,
        reviewer: Callable[[EvolutionBatch], Awaitable[bool]],
    ) -> None:
        self.store = store
        self._reviewer = reviewer
        self._review_lock = asyncio.Lock()

    async def observe(self, observation: EvolutionObservation) -> bool:
        return await self.store.record(observation)

    async def run(
        self,
        scope: EvolutionScope | None = None,
        *,
        force: bool = False,
    ) -> EvolutionBatch | None:
        async with self._review_lock:
            batch = await self.store.claim(scope, force=force)
            if batch is None:
                return None
            try:
                succeeded = await self._reviewer(batch)
            except Exception as exc:
                await self.store.complete(batch, succeeded=False, error=type(exc).__name__)
                raise
            await self.store.complete(
                batch,
                succeeded=succeeded,
                error=None if succeeded else "review did not complete",
            )
            return batch


def observation_from_run(
    *,
    run_id: str,
    session_key: str,
    turn_id: str,
    status: str,
    user_text: str | None,
    report: dict[str, Any],
    run_kind: str = "agent",
) -> EvolutionObservation | None:
    """Apply cheap, deterministic admission rules to one finished Run."""
    text = (user_text or "").strip()
    if (
        status != "succeeded"
        or run_kind in _INTERNAL_RUN_KINDS
        or not text
        or text.startswith("/")
        or _non_negative_int(report.get("model_calls")) == 0
    ):
        return None
    tool_iterations = _non_negative_int(report.get("tool_calls"))
    return EvolutionObservation(
        run_id=run_id,
        session_key=session_key,
        turn_id=turn_id,
        # The reviewer, not a brittle keyword list, decides whether the turn
        # contains a durable fact. The cheap gate only excludes trivial text.
        memory_turns=1 if len(text) >= 8 else 0,
        skill_tool_iterations=tool_iterations,
    )


def _positive_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
