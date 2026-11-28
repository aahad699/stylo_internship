"""Employee-facing replies that must stay identical across answer paths."""

import re

REFUSAL = "I can't answer that. Its in my designation/policy filter"
ROLE_FILTER = "I'm not allowed to ignore the designation filter. I can only share what your role allows."
UNKNOWN = "I couldn't find this in the policies I can open. Ask HR."
TOPIC_MISSING = "{topic}: I couldn't find this. Ask HR."
RATE_MISSING = "The rate isn't available to me. Ask HR."

_GREETING = {
    "hi": "Hello",
    "hello": "Hello",
    "hey": "Hello",
    "hiya": "Hello",
    "howdy": "Hello",
    "good morning": "Good morning",
    "good afternoon": "Good afternoon",
    "good evening": "Good evening",
    "salam": "Hello",
    "salaam": "Hello",
    "aoa": "Hello",
    "assalamualaikum": "Hello",
    "assalamu alaikum": "Hello",
    "assalam o alaikum": "Hello",
    "as salamu alaykum": "Hello",
}
_GOODBYE = {
    "bye": "Goodbye",
    "goodbye": "Goodbye",
    "good bye": "Goodbye",
    "good night": "Good night",
    "goodnight": "Good night",
    "see you": "Goodbye",
    "see you later": "Goodbye",
    "see ya": "Goodbye",
    "cya": "Goodbye",
    "take care": "Goodbye",
    "allah hafiz": "Goodbye",
    "khuda hafiz": "Goodbye",
}


def courtesy_reply(question: str, name: str = "") -> str | None:
    """A hello or goodbye, only when the whole message is that and nothing else."""
    text = re.sub(r"[!?.…,]+", " ", question or "").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    text = re.sub(r"\s+(?:there|team|everyone|all|hr)$", "", text)
    lead = _GREETING.get(text) or _GOODBYE.get(text)
    if lead is None:
        return None
    first = (name or "").strip().split()
    who = f", {first[0]}" if first else ""
    if text in _GOODBYE:
        return f"{lead}{who}. See you next time."
    return f"{lead}{who}. Ask me about leave, travel, or another policy."
