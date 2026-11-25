# HR Desk end-to-end findings

Run against branch `retrieval-quality` (af745ef), the code that produced the traces in the 2026-11-24 database dump. Real Postgres + pgvector data restored from that dump, real OpenAI models. Final run: 227 tests, 123 passed, 104 failed. Policy matrix: 86 of 171 cases pass.

Answers vary between runs because several routing steps call a model. The same question from the same role took different paths in different runs, so treat single-case results as samples and the patterns below as the findings.

## Access control

No content leak was found. Across 35 designations × 6 policies, and 72 RBAC cases, no answer contained a fact from a policy or grade row the asker may not see. Admin routes return 403 to non-admins, signed-out calls return 401, cross-origin writes return 403, sign-in is throttled, chats are private to their owner, and a role change applies to the next question.

The access problems are over-blocking, misleading answers, and one session bug.

1. **Sign-out does not end the session.** `backend/app/store_pg.py:301` deletes `WHERE token = <raw token>`, but `create_session` stores `digest(token)` (line 308), so nothing is deleted. A copied cookie keeps working for 12 hours after sign-out. Reproduced with curl: 200 before sign-out, 200 after, session row still present. `main` has the same code. Unit tests miss it because `store_memory.py` stores raw tokens. Test: `sessions › signing out revokes the token server-side`.
2. **The Provident Fund Loan policy is closed to most Head Office staff.** Its audience in the database is `employee,manager,hr,executive`. Audiences match designations exactly, so officer, senior_manager, AGM, DGM, GM, CEO and others are refused, although §2.1 covers "All Permanent Head Office (RSC) employees". The refusal text says "It is written for a different grade", which is not the reason.
3. **Many roles have no medical plan row.** DGM, group chief, head_hr, assistant_manager, senior_executive, RSM, ASM, store roles, `employee` and `hr` get "couldn't find" for their own medical limits (sweep: 13 of the 22 roles in the Medical audience). Plan mapping in `access.py` (plan table near lines 83-89).
4. **Questions about another grade are answered with the asker's own figures, labelled as the other grade's.** GM asks the COO's daily allowance and gets "COO: 1,800" (the GM rate; the COO rate is 2,000). AGM asks the GM's hotel and gets the AGM/SM hotels. Nothing leaks, but the answer is wrong and looks authoritative. The other-grade check only covers the generic `employee`/`manager` designations.
5. **Questions about a policy you can't open get a false answer from another policy instead of a refusal.** For 10 designations outside the Travel audience, "How many days before travelling must the travel request form be submitted?" is answered from the Advance Salary notes as "the policy does not specify a deadline". The Travel Policy says 7 days.
6. **HR (`hr`, meant to see everything) gets "couldn't find" on PF and Medical**, and "I can't answer that." for the AGM daily allowance.

## Retrieval and answering

7. **Personal benefit questions are swallowed by the benefit overview.** `benefits.py:13-21` treats any question with allowance/benefit + what + my as an overview unless it names a narrow topic, and the narrow list lacks "daily allowance", "hospitalization", "room", "self-stay". "What is my daily allowance?" and "What is my hospitalization benefit?" return a generic list with no figure (10 matrix cases).
8. **A model classifier sends non-travel questions to the travel lookup.** `pipeline.py:317-339`: any question with I/my/me goes to `llm.personal_query`; if it returns fuel/hotel/daily/approval the travel shortcut answers without retrieval. Seen: "How do I apply for an advance salary?" → travel approver; "How much provident fund loan can I take?" → hotel rates. It is non-deterministic, which is why results change between runs.
9. **Correct answers are replaced with "I can't answer that."** `sanitize.py:23-28` refuses any answer containing wording like "policy text you provided" or "provided excerpt", which the model writes when it hedges. Blocked: "Can I bring a lawyer to my inquiry?", non-panel claim deadline, AGM daily allowance, incident report author. The rule is also inconsistent: "The policy excerpt you provided…" passes. (Mechanism inferred from code and the unblocked answers; blocked answer text is not stored.)
10. **Facts that are in the text are not found.** PF loan amount (§5, Annexure A 75/80/85%) retrieves the §14 form and the blank template instead, so even allowed roles get "couldn't find" (4 of 4 in the sweep). "Who counts as a dependent" retrieves the plan table, not §2.2. A general hotel question retrieves §7 Daily Tour Allowance (top score 0.063) and answers "hotel policy not specified". Also missed: gift limit PKR 25,000, minimum hiring age, child cover age, show-cause reply time, "point 5.10", "page 5", Urdu phrasing, typos ("abouit feul").
11. **Follow-ups lose the topic.** "What is my daily allowance on tour?" then "and what about hotel?" (browser test) → "couldn't find". Also "Is it paid?", "And how many installments?", and a "Which policy or plan do you mean?" loop after a refusal.
12. **Blocked questions are traced with an empty question**, so HR can't see what was blocked on the Questions page.

## Files

- `report/matrix-summary.md`: every matrix case with question, answer, expected fact and source.
- `report/visibility/`: per-designation sweep rows. `node lib/visibility-table.mjs` renders the table.
- `playwright-report/`: HTML report with traces for failures (`npx playwright show-report`).
- `cases/policy-cases.json`: 147 cases built from the policy text. `cases/trace-cases.json`: 24 cases replayed from real usage.
