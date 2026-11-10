import pytest

from gateway.engine.mask import Mapping
from gateway.engine.session import DEFAULT_TTL_SECONDS, SessionNotFound, SessionStore
from gateway.engine.types import EntityType


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> SessionStore:
    return SessionStore(ttl_seconds=60, clock=clock)


def test_default_idle_ttl_is_thirty_minutes() -> None:
    assert DEFAULT_TTL_SECONDS == 30 * 60
    assert SessionStore().ttl_seconds == DEFAULT_TTL_SECONDS


def test_ids_are_random_and_long(store: SessionStore) -> None:
    ids = {store.create() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(session_id) >= 32 for session_id in ids)


def test_get_returns_the_same_mapping(store: SessionStore) -> None:
    mapping = Mapping()
    session_id = store.create(mapping)
    assert store.get(session_id) is mapping
    assert isinstance(store.get(store.create()), Mapping)


def test_idle_past_the_ttl_is_gone(store: SessionStore, clock: Clock) -> None:
    session_id = store.create()
    clock.now += 61
    with pytest.raises(SessionNotFound):
        store.get(session_id)
    assert len(store) == 0


def test_use_inside_the_ttl_extends_it(store: SessionStore, clock: Clock) -> None:
    session_id = store.create()
    for _ in range(5):
        clock.now += 50
        store.get(session_id)
    assert len(store) == 1


def test_unknown_id_raises_without_echoing_it(store: SessionStore) -> None:
    with pytest.raises(SessionNotFound) as error:
        store.get("no-such-session")
    assert "no-such-session" not in str(error.value)


def test_delete_clears_the_mapping(store: SessionStore) -> None:
    mapping = Mapping()
    mapping.placeholder_for("Anna", EntityType.PERSON)
    session_id = store.create(mapping)

    assert store.delete(session_id) is True
    assert len(mapping) == 0
    assert store.delete(session_id) is False
    with pytest.raises(SessionNotFound):
        store.get(session_id)


def test_purge_removes_expired_sessions_only(store: SessionStore, clock: Clock) -> None:
    old = store.create(Mapping())
    clock.now += 40
    fresh = store.create()
    clock.now += 30
    assert store.purge_expired() == 1
    assert fresh in store
    assert old not in store


def test_a_non_positive_ttl_is_refused(clock: Clock) -> None:
    with pytest.raises(ValueError, match="ttl"):
        SessionStore(ttl_seconds=0, clock=clock)


@pytest.mark.parametrize("ttl", [float("nan"), float("inf"), float("-inf"), -1])
def test_a_non_finite_or_negative_ttl_is_refused(ttl: float) -> None:
    with pytest.raises(ValueError, match="ttl"):
        SessionStore(ttl_seconds=ttl)


def test_save_of_an_expired_session_refuses_and_forgets_it(
    store: SessionStore, clock: Clock
) -> None:
    mapping = Mapping()
    mapping.placeholder_for("Anna", EntityType.PERSON)
    session_id = store.create(mapping)
    clock.now += 61
    with pytest.raises(SessionNotFound):
        store.save(session_id)
    assert len(mapping) == 0


def test_create_drops_sessions_already_expired(store: SessionStore, clock: Clock) -> None:
    old = Mapping()
    old.placeholder_for("Anna", EntityType.PERSON)
    store.create(old)
    clock.now += 61
    store.create()
    assert len(old) == 0
