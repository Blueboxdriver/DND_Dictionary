from dndref.navigation import NavigationHistory, NavigationState, RecentlyViewed, ViewedRecord
from dndref.search import SearchCategory


def state(identity: str) -> NavigationState:
    return NavigationState(
        category=SearchCategory.SPELLS, query="", mode="names", editions=("2024",),
        sources=(), parent_class=None, challenge_rating=None, creature_type=None,
        size=None, selected_id=identity, variant_id=identity, list_index=0,
        list_scroll=0, detail_scroll=0, detail_id=identity,
    )


def test_back_forward_and_forward_invalidation() -> None:
    history = NavigationHistory()
    for name in "ABC":
        history.navigate_to(state(name))
    assert history.go_back() == state("B")
    assert history.go_back() == state("A")
    assert history.go_forward() == state("B")
    assert history.go_forward() == state("C")
    history.go_back()
    history.navigate_to(state("D"))
    assert not history.can_go_forward


def test_duplicate_restore_exact_identity_edition_and_cap() -> None:
    history = NavigationHistory(limit=2)
    assert history.navigate_to(state("pack-a:spell/fireball"))
    assert not history.navigate_to(state("pack-a:spell/fireball"))
    history.navigate_to(state("pack-b:spell/fireball"))
    history.navigate_to(state("old-pack:spell/old"))
    assert history.go_back() == state("pack-b:spell/fireball")
    history.restore_history_state(state("pack-b:spell/fireball"))
    assert history.current == state("pack-b:spell/fireball")


def test_recently_viewed_moves_revisits_to_top_and_is_bounded() -> None:
    recent = RecentlyViewed(limit=2)
    old_2014 = ViewedRecord("a", SearchCategory.SPELLS, "Fireball", "2014")
    new_2024 = ViewedRecord("b", SearchCategory.SPELLS, "Fireball", "2024")
    third = ViewedRecord("c", SearchCategory.MONSTERS, "Dragon", "2024")
    recent.add(old_2014)
    recent.add(new_2024)
    recent.add(old_2014)
    assert recent.records == (old_2014, new_2024)
    recent.add(third)
    assert recent.records == (third, old_2014)
