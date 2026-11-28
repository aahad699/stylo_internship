"""List benefit families from active, authorized policies instead of a single RAG shortlist."""

from __future__ import annotations

import re
from datetime import date

from .access import (allowed_audiences, can_see_passage, policy_row_roles, role_label, shares_audience,
                     visible_passage)
from .guardrails import contains_named_pay
from .models import Employee, Passage
from .sanitize import exposes_prompt
from .textutil import matched_tokens, token_set

_OVERVIEW = re.compile(
    r"\b(?:facilit(?:y|ies)|benefits?|perks?|entitlements?|allowances?)\b", re.I
)
_BROAD = re.compile(r"\b(?:all|every|list|overview|what|which|available|eligible|qualify|avail)\b", re.I)
_SELF = re.compile(r"\b(?:my|i|for me|for myself)\b", re.I)
# Any other word names the benefit ("daily allowance", "hospitalization benefit"), so it is not an overview.
_OVERVIEW_WORDS = {
    "facility", "facilities", "benefit", "benefits", "perk", "perks", "entitlement", "entitlements",
    "allowance", "allowances", "all", "every", "list", "overview", "which", "available", "eligible",
    "qualify", "avail", "entitled", "am", "have", "any", "there", "myself",
}
_BENEFIT_TITLE = re.compile(
    r"\b(?:benefits?|facilities|allowances?|entitlements?|insurance|takaful|leave|reimbursement|"
    r"provident|loan|pension|retirement|advance salary|scholarship)\b", re.I
)
_HEADINGS = ["eligibility", "scope", "applicability", "coverage", "purpose", "benefits"]
# "medical policy" or "tell me about health" names only the topic, so it asks what the policy gives this role.
_MEDICAL_TOPIC = "medical health insurance takaful"
_POLICY_TOPICS = {"travel": "travel travelling traveling tour", "provident": "provident fund pf loan"}
_TOPIC_FILLER = "policy policies tell about explain info information details overview show give my our plan cover coverage"
_MEDICAL_SCHEDULE = "medical benefit schedule"
_MEDICAL_CLAIMS = ["claims in case of panel", "claim in case of non-panel hospital"]  # The printed heading reads "Panel Hospital1".
_FIELD = re.compile(r"(?:^|\.\s+)([A-Z][^:.]{1,70}):\s*([^.]+)")
_TRAVEL_HEADINGS = ["eligibility", "accommodation", "daily allowance", "fuel entitlement list",
                    "travel expense form", "mode of travel"]


def asks_for_benefit_overview(question: str) -> bool:
    return bool(_OVERVIEW.search(question) and _BROAD.search(question)
                and _SELF.search(question) and token_set(question) <= _OVERVIEW_WORDS)


def benefit_overview(store, employee: Employee, today: date) -> tuple[str, list[Passage]]:
    role = employee.designation
    audiences = set(allowed_audiences(role))
    items: list[str] = []
    sources: dict[str, Passage] = {}

    for policy in store.list_policies():
        if not shares_audience(policy.audience, audiences):
            continue
        title = policy.title.casefold()
        if "medical" not in title and "travel" not in title and not _BENEFIT_TITLE.search(title):
            continue
        travel = "travel" in title
        evidence = [
            p for p in store.heading_hits(_TRAVEL_HEADINGS if travel else _HEADINGS, audiences, today,
                                          policy_ids=[policy.id])
            if p.content_type in ({"section", "table"} if travel else {"section"})
            and shares_audience(p.audience, audiences)
            and visible_passage(role, p)
            and can_see_passage(role, p.title, p.heading, p.body, p.content_type, p.audience)
            and not contains_named_pay(f"{p.title}\n{p.body}")
            and not exposes_prompt(p.body)
        ]
        if not evidence:
            continue
        summary = _summary(policy.title, evidence)
        if summary and summary not in items:
            items.append(summary)
            sources.update((p.id, p) for p in evidence)

    if not items:
        return (f"I couldn't confirm any benefits from the policies available to your designation "
                f"({role_label(role)}). Ask HR to confirm what applies to you.", [])
    return (f"Based on your designation ({role_label(role)}), these are the benefits covered by the policies I can "
            "check. Some still depend on employment status, location, tenure, or other conditions I can't verify.\n\n"
            + "\n".join(f"- {item}" for item in items), list(sources.values()))


def _summary(title: str, evidence: list[Passage]) -> str:
    label = title.casefold()
    body = "\n".join(p.body for p in evidence).casefold()
    if "medical" in label and "permanent management employees" in body:
        options = ["inpatient care"] if re.search(r"\bin[- ]?patient\b", body) else ["medical coverage"]
        if "maternity" in body:
            options.append("maternity benefits if you meet the separate eligibility rules")
        return f"Medical coverage: {' and '.join(options)}. The policy covers permanent management employees."
    if "travel" in label and "permanent management employees" in body:
        distance = re.search(r"distance of ([\d,]+) kilometers or more round trip", body)
        trip = f" of at least {distance[1]} km round trip" if distance else ""
        headings = " ".join(p.heading.casefold() for p in evidence)
        options = [name for heading, name in (
            ("mode of travel", "transport"), ("accommodation", "hotel or self-stay"),
            ("daily allowance", "daily allowances"), ("fuel entitlement", "fuel rates"),
            ("travel expense form", "eligible expense claims"),
        ) if heading in headings]
        options_text = ", ".join(options[:-1]) + " and " + options[-1] if len(options) > 1 else (
            options[0] if options else "support"
        )
        return (f"Business travel: {options_text} for qualifying work trips{trip}. "
                "Permanent management employment is required.")
    if "provident fund" in label and "permanent head office" in body:
        tenure = re.search(r"\((\d+)\) months of continuous service", body)
        extra = f" with at least {int(tenure[1])} months of continuous service" if tenure else ""
        return f"Provident fund loan: available to permanent Head Office (RSC) employees{extra}, subject to policy and approval."
    if "advance salary" in label and re.search(r"\b(?:head office|ho\s*-\s*rsc)\b", label):
        max_pct = re.search(r"maximum\s+(\d+)% of gross salary", body)
        limit = f" up to {max_pct[1]}% of gross salary" if max_pct else ""
        window = re.search(r"application window:\s*(\d+(?:st|nd|rd|th)) to (\d+(?:st|nd|rd|th)) of the month", body)
        dates = f" Apply from the {window[1]} to the {window[2]} each month." if window else ""
        return f"Salary advance:{limit} for eligible Head Office (RSC) employees.{dates}" if limit else (
            f"Salary advance: available to eligible Head Office (RSC) employees.{dates}"
        )
    return f"{title}: a policy is available, but I can't confirm your eligibility from your designation alone."


def asks_for_medical_overview(question: str) -> bool:
    words = token_set(question)
    vocabulary = f"{_MEDICAL_TOPIC} {_TOPIC_FILLER}"
    leftover = words - matched_tokens(words, vocabulary)
    return (bool(words) and bool(matched_tokens(words, _MEDICAL_TOPIC))
            and all(_one_swap(word, vocabulary.split()) for word in leftover))


def asks_for_policy_overview(question: str) -> str:
    words = token_set(question)
    for name, topic in _POLICY_TOPICS.items():
        vocabulary = f"{topic} {_TOPIC_FILLER}"
        if words and matched_tokens(words, topic) and not (words - matched_tokens(words, vocabulary)):
            return name
    return ""


def policy_overview(store, employee: Employee, today: date, topic: str) -> tuple[str, list[Passage]] | None:
    """Summarize only visible, current sections and the employee's own travel rows."""
    role = employee.designation
    audiences = set(allowed_audiences(role))
    policy = next((p for p in store.list_policies()
                   if topic in p.title.casefold() and shares_audience(p.audience, audiences)), None)
    if policy is None:
        return None
    headings = _TRAVEL_HEADINGS if topic == "travel" else ["eligibility", "entitlement", "installment"]
    evidence = [p for p in store.heading_hits(headings, audiences, today, policy_ids=[policy.id])
                if p.content_type in {"section", "table"} and shares_audience(p.audience, audiences)
                and visible_passage(role, p)
                and can_see_passage(role, p.title, p.heading, p.body, p.content_type, p.audience)
                and not (topic == "travel" and role not in {"regional_sales_manager", "area_sales_manager", "hr"}
                         and re.search(r"field[- ]based", p.heading, re.I))
                and not contains_named_pay(f"{p.title}\n{p.body}") and not exposes_prompt(p.body)]
    if not evidence:
        return None
    lines = []
    for p in evidence[:6]:
        heading = _display_heading(p.heading)
        body = _display_body(_clauses(p.body, 1))
        if heading and body:
            lines.append(f"- {heading}: {body}")
    return (f"{policy.title}, for your designation ({role_label(role)}):\n\n" + "\n".join(lines), evidence[:6])


def _one_swap(word: str, vocabulary: list[str]) -> bool:
    """"ploicy" is "policy" with two neighbouring letters swapped."""
    return any(len(word) == len(known) and any(
        word[:i] + word[i + 1] + word[i] + word[i + 2:] == known for i in range(len(word) - 1)
    ) for known in vocabulary)


def medical_overview(store, employee: Employee, today: date) -> tuple[str, list[Passage]] | None:
    """Who is covered, the asker's own plan row, and how to claim. None when the role can't open the policy."""
    role = employee.designation
    audiences = set(allowed_audiences(role))
    policy = next((p for p in store.list_policies()
                   if "medical" in p.title.casefold() and shares_audience(p.audience, audiences)), None)
    if policy is None:
        return None

    def hits(headings: list[str], kind: str) -> list[Passage]:
        return [p for p in store.heading_hits(headings, audiences, today, policy_ids=[policy.id])
                if p.content_type == kind and shares_audience(p.audience, audiences) and visible_passage(role, p)
                and not contains_named_pay(f"{p.title}\n{p.body}") and not exposes_prompt(p.body)]

    eligibility = hits(["eligibility"], "section")
    rows = sorted((p for p in hits([_MEDICAL_SCHEDULE], "table")
                   if role == "hr" or role in (policy_row_roles(p.heading, p.body, p.title) or set())),
                  key=lambda p: _plan_line(p))
    claims = {heading: [p for p in hits([heading], "section") if p.heading.casefold().find(heading) >= 0]
              for heading in _MEDICAL_CLAIMS}
    lines = []
    if eligibility:
        lines.append(f"Who is covered: {_clauses(eligibility[0].body, 2)}")
    if rows:
        lines.extend(_plan_line(row) for row in rows)
    else:
        lines.append(f"Your plan: the benefit schedule doesn't list a plan for the {role_label(role)} designation. "
                     "Ask HR which plan applies to you.")
    for heading, where in zip(_MEDICAL_CLAIMS, ("a panel hospital", "a non-panel hospital")):
        if claims[heading]:
            lines.append(f"Claims at {where}: {_clauses(_earliest(claims[heading]).body, 1)}")
    answer = (f"{policy.title}, for your designation ({role_label(role)}):\n\n"
              + "\n".join(f"- {line}" for line in lines)
              + "\n\nAsk about any part, such as maternity, exclusions, or a non-panel claim, for the details.")
    return answer, [*eligibility, *rows, *(p for found in claims.values() for p in found)]


def _plan_line(row: Passage) -> str:
    fields = [(label.strip(), value.strip()) for label, value in _FIELD.findall(row.body)]
    plan = next((value for label, value in fields if label.casefold() == "plan"), "Your plan")
    figures = "; ".join(f"{label} {value}" for label, value in fields if label.casefold() not in {"plan", "grades"})
    return f"{plan}: {figures}." if figures else f"{plan}."


def _display_heading(heading: str) -> str:
    text = re.sub(r"\bannexures?\s+[a-z0-9]+\b\s*[:.-]?\s*", "", heading or "", flags=re.I)
    return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", text).strip(" :.-")


def _display_body(text: str) -> str:
    cleaned = re.sub(r"\bannexures?\s+[a-z0-9]+\b", "the policy", text or "", flags=re.I)
    cleaned = re.sub(r"(?i)(?:please\s+)?refer to the following\b[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:")
    return f"{cleaned}." if cleaned else ""


def _clauses(text: str, count: int) -> str:
    """The first numbered clauses, such as 2.1 and 2.2, without their numbers. Word bullet glyphs become separators."""
    flat = re.sub(r"\s*;\s*", "; ", re.sub(r"\s+", " ", text.replace("", ";"))).strip()
    parts = [part for part in re.split(r"\s*\b\d+(?:\.\d+)+\.\s+", flat) if part.strip(" ;")]
    kept = [re.sub(r":\s*;\s*", ": ", part).strip(" ;").rstrip(".") for part in parts[:count]]
    return ". ".join(kept) + "."


def _earliest(passages: list[Passage]) -> Passage:
    """A section split across chunks starts in the chunk holding its first clause."""
    return min(passages, key=lambda p: [int(n) for n in re.findall(r"\d+", (re.search(r"\d+(?:\.\d+)+", p.body) or [""])[0])])
