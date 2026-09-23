"""Stable, in-memory browser history state independent of Textual widgets."""

from __future__ import annotations

from dataclasses import dataclass

from .search import SearchCategory, SourceIdentity


@dataclass(frozen=True)
class NavigationState:
    category: SearchCategory
    query: str
    mode: str
    editions: tuple[str, ...]
    sources: tuple[SourceIdentity, ...]
    parent_class: str | None
    challenge_rating: str | None
    creature_type: str | None
    size: str | None
    selected_id: str | None
    variant_id: str | None
    list_index: int
    list_scroll: int
    detail_scroll: int
    detail_id: str | None
    narrow_detail_open: bool = False

    @property
    def logical_key(self) -> tuple[object, ...]:
        return (
            self.category, self.query, self.mode, self.editions, self.sources,
            self.parent_class, self.challenge_rating, self.creature_type, self.size,
            self.selected_id, self.variant_id, self.list_index, self.list_scroll,
            self.detail_scroll,
            self.detail_id, self.narrow_detail_open,
        )


class NavigationHistory:
    def __init__(self, limit: int = 100) -> None:
        self.limit = max(1, limit)
        self.current: NavigationState | None = None
        self._back: list[NavigationState] = []
        self._forward: list[NavigationState] = []

    @property
    def can_go_back(self) -> bool:
        return bool(self._back)

    @property
    def can_go_forward(self) -> bool:
        return bool(self._forward)

    def navigate_to(self, state: NavigationState) -> bool:
        if self.current is not None and self.current.logical_key == state.logical_key:
            return False
        if self.current is not None:
            self._back.append(self.current)
            del self._back[:-self.limit]
        self.current = state
        self._forward.clear()
        return True

    def go_back(self) -> NavigationState | None:
        if not self._back:
            return None
        if self.current is not None:
            self._forward.append(self.current)
            del self._forward[:-self.limit]
        self.current = self._back.pop()
        return self.current

    def go_forward(self) -> NavigationState | None:
        if not self._forward:
            return None
        if self.current is not None:
            self._back.append(self.current)
            del self._back[:-self.limit]
        self.current = self._forward.pop()
        return self.current

    def restore_history_state(self, state: NavigationState) -> None:
        """Set the displayed state after Back/Forward without adding a new visit."""
        self.current = state


@dataclass(frozen=True)
class ViewedRecord:
    identity: str
    category: SearchCategory
    name: str
    edition: str | None


class RecentlyViewed:
    def __init__(self, limit: int = 30) -> None:
        self.limit = max(1, limit)
        self._records: list[ViewedRecord] = []

    @property
    def records(self) -> tuple[ViewedRecord, ...]:
        return tuple(self._records)

    def add(self, record: ViewedRecord) -> None:
        self._records = [item for item in self._records if item.identity != record.identity]
        self._records.insert(0, record)
        del self._records[self.limit:]
