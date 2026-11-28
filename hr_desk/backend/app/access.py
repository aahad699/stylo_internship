"""Who can see which policies. Compartments, not a ladder. HR sees every policy."""

from __future__ import annotations

import hashlib
import re
from contextvars import ContextVar

from .textutil import _stem, matched_tokens, token_set

ROLE_LABELS = {
    "intern": "Intern",
    "contract": "Contract",
    "employee": "Employee",
    "manager": "Manager",
    "hr": "HR",
    "executive": "Executive",
    "trainee": "Trainee",
    "officer": "Officer",
    "senior_executive": "Senior Executive",
    "assistant_manager": "Assistant Manager",
    "senior_manager": "Senior Manager",
    "assistant_general_manager": "Assistant General Manager",
    "deputy_general_manager": "Deputy General Manager",
    "general_manager": "General Manager",
    "group_chief": "Group Chief",
    "coo_business": "COO (Business)",
    "ceo": "CEO",
    "cfo": "CFO",
    "director": "Director",
    "designer": "Designer",
    "supervisor": "Supervisor",
    "senior_store_manager": "Senior Store Manager",
    "store_manager": "Store Manager",
    "assistant_store_manager": "Assistant Store Manager",
    "branch_accountant": "Branch Accountant",
    "salesman": "Salesman",
    "regional_sales_manager": "Regional Sales Manager",
    "area_sales_manager": "Area Sales Manager",
    "retail_excellence_manager": "Retail Excellence Manager",
    "people_executive": "People Executive",
    "people_specialist": "People Specialist",
    "regional_people_specialist": "Regional People Specialist",
    "hrbp": "HR Business Partner",
    "head_hr": "Head of HR",
    "support_staff": "Support Staff",
    "other_staff": "Other Staff",
}

AUDIENCES = ("everyone", *ROLE_LABELS)
AUDIENCE_LABELS = {
    **ROLE_LABELS,
    "everyone": "Everyone",
    "intern": "Interns",
    "employee": "Employees",
    "manager": "Managers",
    "hr": "HR",
    "executive": "Executives",
}

_TRAVEL_ROLES = set("""
coo_business group_chief general_manager deputy_general_manager assistant_general_manager senior_manager
manager assistant_manager senior_executive executive officer regional_sales_manager area_sales_manager
""".split())
_MEDICAL_ROLES = _TRAVEL_ROLES | set("""
ceo cfo director designer supervisor support_staff other_staff senior_store_manager store_manager
assistant_store_manager branch_accountant head_hr
""".split())
# Store staff work in retail and the Travel Policy calls RSMs and ASMs field-based,
# so none of them is a Head Office (RSC) employee.
# Intern (L1), Contract (C), and trainees are not permanent employees.
_HEAD_OFFICE_ROLES = set(ROLE_LABELS) - set("""
intern contract trainee salesman senior_store_manager store_manager assistant_store_manager branch_accountant
regional_sales_manager area_sales_manager
""".split())


def reference_audience(title: str) -> str:
    title = title.casefold()
    if title.startswith("travel"):
        return pack_audiences(list(_TRAVEL_ROLES | {"employee", "hr"}))
    if title.startswith("medical"):
        return pack_audiences(list(_MEDICAL_ROLES | {"employee", "hr"}))
    if title.startswith("provident") or "advance salary" in title:
        return pack_audiences(list(_HEAD_OFFICE_ROLES))
    return "everyone"


_LOW_TRAVEL = {"senior_executive", "executive", "officer", "assistant_manager", "manager"}
_SM_AGM = {"senior_manager", "assistant_general_manager"}
# Annexure B grade labels. Plan A's "HEAD" and "Higher Management" take in the
# DGM, the group chief and the head of HR; "Assistant" in Plan B is the assistant
# manager; "Lower Management" in Plan C is the senior executive.
_PLANS = {
    "a": {"coo_business", "director", "ceo", "cfo", "assistant_general_manager", "deputy_general_manager",
          "general_manager", "group_chief", "head_hr"},
    "b": {"manager", "assistant_manager", "senior_manager"},
    "c": {"executive", "senior_executive", "designer"},
    "d": {"officer", "supervisor", "support_staff"},
    "e": {"other_staff"},
}


def has_medical_plan(designation: str) -> bool:
    return any(designation in roles for roles in _PLANS.values())


# Store staff and field sales start as retail. Intern, contract, and trainee start as not permanent.
_RETAIL_ROLES = {
    "salesman", "senior_store_manager", "store_manager", "assistant_store_manager",
    "branch_accountant", "regional_sales_manager", "area_sales_manager",
}
_TEMPORARY_ROLES = {"intern", "contract", "trainee"}
_employee_access: ContextVar[tuple[str, bool] | None] = ContextVar("hr_desk_employee_access", default=None)


def default_employee_type(designation: str) -> str:
    return "retail" if designation in _RETAIL_ROLES else "head_office"


def default_is_permanent(designation: str) -> bool:
    return designation not in _TEMPORARY_ROLES


def default_medical_plan(designation: str) -> str:
    for letter, roles in _PLANS.items():
        if designation in roles:
            return letter
    return ""


def bind_employee_access(employee) -> object:
    """The ask in progress. Passage checks read the stored place of work and permanence."""
    return _employee_access.set((employee.employee_type, bool(employee.is_permanent)))


def reset_employee_access(token) -> None:
    _employee_access.reset(token)


def _resolved_access(designation: str, employee_type, is_permanent) -> tuple[str, bool]:
    bound = _employee_access.get()
    if employee_type is None:
        employee_type = bound[0] if bound else default_employee_type(designation)
    if is_permanent is None:
        is_permanent = bound[1] if bound else default_is_permanent(designation)
    return employee_type, bool(is_permanent)


_LABEL = re.compile(r"(?:^|[.\n]\s*)(?:grade entitlement|grade):\s*([^\.\n]+)", re.I)
_PLAN = re.compile(r"\bplan\s+([a-e])\b", re.I)


def policy_row_roles(heading: str, body: str, title: str = "") -> set[str] | None:
    """Interpret only recognizable grade cells, not words elsewhere in a row."""
    label = heading.casefold()
    title = title.casefold()
    if "medical benefit schedule" in label or "medical" in title:
        plan = _PLAN.search(heading) or re.match(r"plan:\s*plan\s+([a-e])\b", body, re.I) or _PLAN.match(body.strip())
        if plan:
            return _PLANS[plan[1].lower()]
    if "travel" not in title and not any(
        marker in label for marker in ("authorization of travel", "fuel entitlement", "mode of travel", "hotel entitlement")
    ):
        return None
    if "tour allowance" in label:
        for role in ("regional_sales_manager", "area_sales_manager"):
            if ROLE_LABELS[role].casefold() in body.casefold():
                return {role}
        return None
    match = _LABEL.search(body)
    if not match:
        return None
    grades = match[1].casefold()
    if "all levels" in grades:
        return _TRAVEL_ROLES | {"employee"}
    if re.fullmatch(r"o\s+to\s+m", grades):
        return _LOW_TRAVEL | {"manager", "assistant_manager"}
    if re.search(r"\bsm\s*/\s*agm\b", grades):
        return _SM_AGM
    found = set()
    aliases = {
        "coo_business": r"coo(?:\s*\(business\))?",
        "group_chief": r"group chief|gc",
        "general_manager": r"general manager|gm",
        "deputy_general_manager": r"deputy general manager|dgm",
        "assistant_general_manager": r"assistant general manager|agm",
        "senior_manager": r"senior manager|sm",
        "manager": r"manager|m",
        "assistant_manager": r"assistant manager|am",
        "senior_executive": r"senior executive|se",
        "executive": r"executive",
        "officer": r"officers?|o",
    }
    # Consume longer job titles before testing their shorter suffixes.
    for role in ("assistant_general_manager", "deputy_general_manager", "general_manager",
                 "assistant_manager", "senior_manager", "senior_executive", "manager",
                 "executive", "officer", "group_chief", "coo_business"):
        pattern = rf"\b(?:{aliases[role]})(?!\w)"
        if re.search(pattern, grades):
            found.add(role)
            grades = re.sub(pattern, " ", grades)
    if "below" in grades and "senior_executive" in found:
        found |= _LOW_TRAVEL
    return found or None


def pack_audiences(roles: list[str]) -> str:
    """Store several roles in one field. Everyone already covers every role."""
    cleaned = [role.strip() for role in roles if role and role.strip()]
    if not cleaned or any(role not in AUDIENCES for role in cleaned):
        return ""
    if "everyone" in cleaned:
        return "everyone"
    return ",".join(role for role in AUDIENCES if role in cleaned)


def policy_visible_to(
    designation: str, title: str, audience: str, *, employee_type: str | None = None, is_permanent: bool | None = None,
) -> bool:
    """Provident and advance salary open for a permanent head-office employee. Travel and medical stay closed when the employee is not permanent."""
    if designation == "hr":
        return True
    employee_type, is_permanent = _resolved_access(designation, employee_type, is_permanent)
    folded = (title or "").casefold()
    roles = {part for part in (audience or "").split(",") if part}
    if "provident" in folded or "advance" in folded:
        return is_permanent and employee_type == "head_office"
    # An admin who ticks Intern, Contract, or Trainee on that document opens it. A saved role
    # inside the default audience does not make a non-permanent employee permanent.
    opened = designation in _TEMPORARY_ROLES and designation in roles and "everyone" not in roles
    if not is_permanent and any(name in folded for name in ("travel", "medical")) and not opened:
        return False
    return shares_audience(audience, set(allowed_audiences(designation)))


def grade_row_allows(designation: str, heading: str, body: str, title: str, content_type: str) -> bool:
    """A grade row stays with the grades it names, in either the short or full form."""
    if designation == "hr" or content_type != "table":
        return True
    exact = policy_row_roles(heading, body, title)
    if exact is not None:
        return designation in exact
    text = f"{heading} {body}"
    if not re.search(r"\bgrade\b", text, re.I):
        return True
    found = named_grades(text)
    return not found or designation in found


def shares_audience(stored: str, allowed: set[str]) -> bool:
    roles = {part for part in (stored or "").split(",") if part}
    return bool(roles & allowed)


def allowed_audiences(designation: str) -> tuple[str, ...]:
    if designation == "hr":
        return AUDIENCES
    if designation in AUDIENCES and designation != "everyone":
        return ("everyone", designation)
    return ("everyone",)


def access_key(
    designation: str, employee_type: str | None = None, is_permanent: bool | None = None,
) -> str:
    employee_type, is_permanent = _resolved_access(designation, employee_type, is_permanent)
    raw = "|".join(allowed_audiences(designation)) + f"|{employee_type}|{int(is_permanent)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def role_label(designation: str) -> str:
    return ROLE_LABELS.get(designation, designation)


# Travel 2.1 and medical 2.1 cover permanent management employees.
# Provident and advance salary cover permanent Head Office employees.
# An intern is outside those policies. HR administers every grade.
# Grade rows stay in compartments: a manager does not see an executive row.
_NOT_FOR_INTERN = ("travel policy", "medical", "provident", "advance salary")
_GRADE_BANDS = (
    (
        "executive",
        (
            "higher management",
            "plan a",
            "gc / coo",
            "gc/coo",
            "coo (business)",
            "deputy general manager",
            "dgm",
            "general manager",
            "gm / gc",
        ),
    ),
    (
        "manager",
        (
            "middle management",
            "plan b",
            "sm / agm",
            "sm/agm",
            "senior manager",
            "assistant general manager",
            "assistant manager",
        ),
    ),
    (
        "employee",
        (
            "lower management",
            "plan c",
            "plan d",
            "plan e",
            "se & below",
            "se and below",
            "senior executive",
            "o to m",
            "other staff",
            "support staff",
            "officers",
        ),
    ),
)


def grade_band(text: str) -> str | None:
    folded = f" {(text or '').lower()} "
    if "all level" in folded or "all grade" in folded:
        return "all"
    for band, markers in _GRADE_BANDS:
        if any(re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", folded) for marker in markers):
            return band
    return None


_BROAD = {
    "employee",
    "employees",
    "company",
    "department",
    "staff",
    "shall",
    "must",
    "following",
    "applicable",
    "info",
    "information",
    "details",
    "their",
    "they",
    "have",
    "will",
    "been",
    "with",
    "from",
    "your",
    "this",
    "that",
    "about",
    "new",
    "long",
}


def asks_for_hidden(question: str, passages) -> bool:
    """A role refusal needs the question to actually name the closed passage.

    A substantive word in the title or heading is enough. Body matches need
    two distinct words (allowing inflections, but not fuzzy spelling matches).
    """
    words = {word for word in token_set(question) if len(word) >= 3 and word not in _BROAD}
    if not words:
        return False
    for passage in passages:
        if matched_tokens(words, f"{getattr(passage, 'title', '')} {getattr(passage, 'heading', '')}"):
            return True
        body_stems = {_stem(word) for word in token_set(getattr(passage, "body", ""))}
        if len({_stem(word) for word in words} & body_stems) >= 2:
            return True
    return False


# Grade names as the policy rows and annexures write them. Combined row labels
# come first so "SM / AGM" reads as one row, not as two separate grades.
_GRADE_NAMES = [
    (r"se\s*(?:&|and)\s*below", _LOW_TRAVEL),
    (r"sm\s*/\s*agm", _SM_AGM),
    (r"am\s*(?:&|and)\s*m", {"assistant_manager", "manager"}),
    (r"dgm\s+to\s+gm", {"deputy_general_manager", "general_manager"}),
    (r"(?:coo\s*/\s*gc|gc\s*/\s*coo)", {"coo_business", "group_chief"}),
    *sorted(
        ((re.escape(label.casefold()), {role}) for role, label in ROLE_LABELS.items() if role not in {"employee", "hr"}),
        key=lambda item: -len(item[0]),
    ),
    (r"intern\s*\(\s*l1\s*\)", {"intern"}),
    (r"contract(?:ual)?(?:\s+employees?)?(?:\s*\(\s*c\s*\))?", {"contract"}),
    (r"salesmen", {"salesman"}),
    (r"coo", {"coo_business"}),
    *((name, {role}) for name, role in (
        ("dgm", "deputy_general_manager"), ("agm", "assistant_general_manager"), ("gm", "general_manager"),
        ("gc", "group_chief"), ("rsm", "regional_sales_manager"), ("asm", "area_sales_manager"),
    )),
]
# Two-letter grades that are also ordinary words ("I am") count only in capitals.
_CAPITAL_GRADES = [("AM", {"assistant_manager"}), ("SM", {"senior_manager"}), ("SE", {"senior_executive"})]
_PLURAL = r"(?:s|es)?(?:'s|’s|'|’)?(?!\w)"


def named_grades(question: str) -> set[str]:
    """Designations a question names, read longest name first."""
    text = question or ""
    found: set[str] = set()
    for names, flags in ((_GRADE_NAMES, re.I), (_CAPITAL_GRADES, 0)):
        for name, roles in names:
            pattern = re.compile(rf"\b{name}{_PLURAL}", flags)
            if pattern.search(text):
                found |= roles
                text = pattern.sub(" ", text)
    return found


def names_other_grade(designation: str, question: str) -> bool:
    """The question names a grade, and the asker is not in it."""
    if designation == "hr":
        return False
    named = named_grades(question)
    if named:
        return designation not in named
    band = grade_band(question)
    return designation in {"employee", "manager", "intern", "trainee"} and band not in {None, "all", designation}


def role_reason(designation: str, passages) -> str:
    """Why this role stops here. The hidden text itself stays out of the answer."""
    by_title: dict[str, object] = {}
    for passage in passages:
        title = getattr(passage, "title", "") or ""
        if title:
            by_title.setdefault(title, passage)
    if not by_title:
        return "I can't share that. It sits outside your role."
    reasons = []
    for title, passage in by_title.items():
        outside = not shares_audience(getattr(passage, "audience", "everyone"), set(allowed_audiences(designation)))
        reason = _boundary(designation, title, outside)
        if reason not in reasons:
            reasons.append(reason)
    return " ".join(reasons)


_POLICY_SCOPES = (
    ("travel", "the travel rules. That policy applies to permanent management employees"),
    ("medical", "the medical cover. That policy applies to permanent management employees"),
    ("provident", "the provident fund rules. They apply to permanent Head Office employees"),
    ("advance", "the advance-salary steps. They apply to permanent Head Office employees"),
)


def _boundary(designation: str, title: str, outside_audience: bool) -> str:
    """outside_audience: the policy itself is closed to this role, not just a grade row."""
    employee_type, is_permanent = _resolved_access(designation, None, None)
    folded = title.lower()
    who = "an internship" if designation == "intern" else f"the {role_label(designation)} designation"
    for marker, scope in _POLICY_SCOPES:
        if marker not in folded:
            continue
        if marker in {"provident", "advance"}:
            closed = not (is_permanent and employee_type == "head_office")
        else:
            closed = not is_permanent
        if closed or designation == "intern" or outside_audience:
            return f"I can't share {scope}, and {who} is outside that group."
    if designation != "intern" and not outside_audience:
        return "I can't share that. It is written for a different grade, and your role doesn't include that grade."
    if designation == "intern":
        return "I can't share that. An internship doesn't include this policy."
    return f"I can't share that. The {title} isn't open to the {role_label(designation)} designation. Ask HR if it should be."


def can_see_passage(
    designation: str,
    title: str,
    heading: str,
    body: str,
    content_type: str,
    audience: str = "everyone",
) -> bool:
    if designation == "hr":
        return True
    _, is_permanent = _resolved_access(designation, None, None)
    roles = {part for part in (audience or "").split(",") if part}
    label = f"{title} {heading}".lower()
    # An admin who ticks Intern, Contract, or Trainee on that document opens it.
    opened = designation in _TEMPORARY_ROLES and designation in roles and "everyone" not in roles
    if not is_permanent and any(name in label for name in _NOT_FOR_INTERN) and not opened:
        return False
    if content_type == "table":
        exact = policy_row_roles(heading, body, title)
        if exact is not None:
            return designation in exact
        band = grade_band(f"{heading} {body}")
        if band == "all":
            return designation in {"employee", "manager", "executive"}
        if band and designation != band:
            return False
    return True


def passage_grades(heading: str, body: str, content_type: str, title: str = "") -> list[str]:
    if content_type != "table":
        return list(ROLE_LABELS)
    exact = policy_row_roles(heading, body, title)
    if exact is not None:
        return [role for role in ROLE_LABELS if role in exact or role == "hr"]
    band = grade_band(f"{heading} {body}")
    if band == "all":
        return ["employee", "manager", "executive", "hr"]
    if band:
        return [band, "hr"]
    return list(ROLE_LABELS)


def query_role(audiences: set[str]) -> str:
    return "hr" if "hr" in audiences else next((role for role in ROLE_LABELS if role in audiences), "")


def visible_passage(designation: str, passage) -> bool:
    if passage.allowed_roles is not None:
        return designation in passage.allowed_roles
    return can_see_passage(designation, passage.title, passage.heading, passage.body,
                           passage.content_type, passage.audience)
