"""Saved chats can be renamed, and more than one can stay pinned at the top."""

from app.store_memory import MemoryStore

EMAIL = "sara.intern@stylo.example"


def _chat(store, email=EMAIL):
    key = store.create_conversation(email)
    store.append_exchange(
        email, key,
        {"role": "user", "text": "What is in the code of conduct?"},
        {"role": "assistant", "text": "It sets out how we work."},
    )
    return key


def test_a_custom_name_replaces_the_first_question():
    store = MemoryStore()
    key = _chat(store)
    assert store.list_conversations(EMAIL)[0]["title"] == "What is in the code of conduct?"
    saved = store.update_conversation(EMAIL, key, title="Code of conduct")
    assert saved["title"] == "Code of conduct"
    assert saved["pinned"] is False
    assert store.list_conversations(EMAIL)[0]["title"] == "Code of conduct"


def test_several_chats_can_be_pinned_and_the_newest_pin_is_first():
    store = MemoryStore()
    first = _chat(store)
    second = _chat(store)
    third = _chat(store)
    store.update_conversation(EMAIL, first, pinned=True)
    store.conversation_meta[(EMAIL, first)]["pinnedAt"] = "2026-11-27T10:00:00+00:00"
    store.update_conversation(EMAIL, second, pinned=True)
    store.conversation_meta[(EMAIL, second)]["pinnedAt"] = "2026-11-27T11:00:00+00:00"
    rows = store.list_conversations(EMAIL)
    assert [row["id"] for row in rows[:2]] == [second, first]
    assert all(row["pinned"] for row in rows[:2])
    assert rows[2]["id"] == third
    assert rows[2]["pinned"] is False
    unpinned = store.update_conversation(EMAIL, first, pinned=False)
    assert unpinned["pinned"] is False
    again = store.list_conversations(EMAIL)
    assert again[0]["id"] == second
    assert again[0]["pinned"] is True
    assert first in [row["id"] for row in again if not row["pinned"]]
