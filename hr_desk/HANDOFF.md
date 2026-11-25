# HR Desk handoff

Uncommitted working note. Do not commit.

## Goal

The user owns `hr_desk/` only, inside the `stylo_internship` repo. They want HR Desk's retrieval and role-based access control (RBAC) to be correct. The method so far:

1. Run a Playwright end-to-end (E2E) suite against the real app.
2. Find defects.
3. Fix each one test-first.
4. Rerun the suite to prove the fix.

Next session: continue from where this stands. The user picks the next defect, or asks to run the app.

## Current state

- **Branch.** `hr-desk-fixes`, local only and not pushed. It was created from `retrieval-quality` (af745ef). `main` is untouched. See `git log --oneline retrieval-quality..hr-desk-fixes` for the 29 commits. Each fix follows the pattern "Add failing test: …" then the fix commit.
- **Servers are stopped.** Claude Code shut down the API (:8000) and the web app (:3000) for low memory while the session was idle. Start them only when the user asks; restart commands are under Next steps. The database container `hr_desk-db-1` (pgvector, port 5433) was up and healthy.
- **Database.** The local Docker database holds a restore of the user's dump, `../dump-hr_desk-202611242025.sql` (untracked, at the repo root; don't commit it). The access fix's migration has been applied, so the Provident Fund Loan and Advance Salary policies are now open to Head Office staff.
- **Tests.** The backend tests (`hr_desk/backend/evals/`) give 85 passed and 163 subtests passed. The Postgres sign-out test needs `HR_DESK_TEST_DATABASE_URL=postgresql://hr_desk:hr_desk@127.0.0.1:5433/hr_desk_sessions_test`.
- **Last E2E run.** 186 of 227 tests pass. The policy matrix passes 130 of 171 cases. The access sweep shows 0 leaks across 35 designations × 6 policies. Results vary between runs because several routing steps call a model, so about ±2 is noise.
- **Untracked work.** `hr_desk/e2e/`, the Playwright suite, is untracked. The user hasn't asked to commit it.

## Files in play

- **Findings and suite docs.**
  - `hr_desk/e2e/FINDINGS.md` has the original defect list, #1–#12; most are now fixed.
  - `hr_desk/e2e/README.md` explains how to run the suite.
- **E2E suite.**
  - `hr_desk/e2e/tests/*.spec.ts` has three projects: `ui`, `access` and `matrix`.
  - `hr_desk/e2e/cases/*.json` holds 171 cases.
  - `hr_desk/e2e/lib/summarize.mjs` writes `report/matrix-summary.md`.
  - `hr_desk/e2e/lib/visibility-table.mjs` writes the RBAC table.
  - `hr_desk/e2e/report-run-*.log` holds the logs from each run.
- **Backend code changed on this branch.**
  - `app/access.py`: audiences, medical plans, and grade detection (`named_grades`, `names_other_grade`).
  - `app/pipeline.py`: the routing gate, early grade refusal, closed-policy refusal, HR row filter, and the medical overview hook.
  - `app/benefits.py`: the benefit overview and the new `asks_for_medical_overview` / `medical_overview`.
  - `app/retrieve.py`: rank fusion and the `_keep` gate plus the `VECTOR_LEAD` exception.
  - `app/config.py`: `VECTOR_LEAD = 0.05`.
  - `app/sanitize.py`, `app/store_pg.py`, `app/clauses.py`, `app/rewrite.py`, `app/textutil.py`.
  - `data/retrieval_aliases.json`: new synonym groups for advocate/lawyer and outstanding/unpaid, plus the single words medical and health.
- **New backend tests.** All under `backend/evals/`:
  - `test_access_scope.py`
  - `test_routing.py`
  - `test_output_guard.py`
  - `test_answer_contract.py`
  - `test_retrieval.py`
  - `test_followups.py`
  - `test_grade_followups.py`
  - `test_closed_policy_rewrite.py`
  - `test_synonyms.py`, with `fixtures/synonym_candidates.json`
  - `test_medical_overview.py`
  - `test_session_revocation.py`
- **Copy databases left behind.** `hr_desk_access`, `hr_desk_sessions_test` and `hr_desk_retrieval`, all in the same container. Drop them only if the user asks.

## What has been tried

These are all fixed, with failing tests committed first:

- **Sign-out.** It didn't revoke the session (`store_pg.delete_session` hashed nothing).
- **Audiences.** Provident Fund and Advance Salary audiences were too narrow, medical plan rows were missing, and refusal messages were misleading.
- **Routing.**
  - Benefit-overview hijack.
  - The model classifier sent non-travel questions to the travel lookup.
- **Answer filter.** It replaced correct answers that contained hedges like "the text you provided" with "I can't answer that".
- **Other grades.** Questions naming another grade got the asker's own row, and closed policies were answered from the wrong policy.
- **Retrieval misses and lost follow-up topics** (from the retrieval agent's merge).
- **Grade follow-ups and HR rows.**
  - An officer's follow-up "And for a General Manager?" wasn't refused.
  - HR asking about a Senior Manager got the AM & M hotel row.
- **Show-cause question.** A store manager asking the show-cause question was refused on travel. The cause: the model's rewrite named the Travel Policy, and the closed-policy check searched on that rewrite.
- **Lawyer question.** It returned "not specified". The `_keep` word gate dropped §6.6 because it shared 0.33 of the words against a 0.34 cut. Fixed with `VECTOR_LEAD` plus the advocate synonym.
- **Second Provident Fund loan.** The answer allowed one. Fixed with the "unpaid" → "outstanding" synonym so §4.3 is retrieved.
- **Bare "medical policy" / "health policy" questions** returned "couldn't find". There's now a deterministic role overview, and medical and health are single-word synonyms.

Things that failed and were reverted:

- **Rewording the AI instructions to stop "the text you provided" hedges.** This didn't reduce them.
- **Keeping the best vector match unconditionally.** This broke unanswerable questions, so it was narrowed to a clear vector lead of at least 0.05.
- **Adding many synonym phrases at once.** This lowered the keyword-overlap fraction and made retrieval worse.

## Current best theory

The remaining failures are mostly model variance from `gpt-5-nano` and gaps in keyword search, not access-control bugs:

- **Keyword candidates rarely match.** `store_pg.candidates` uses `plainto_tsquery`, which ANDs every word, so the keyword half seldom contributes candidates. Vocabulary gaps (lawyer/advocate, unpaid/outstanding) then hurt.
- **The `_keep` gate is a lexical fraction over the expanded query.** Adding synonyms dilutes it.
- **Known open items.**
  - `fixed_reply` misses a refusal that has a short lead-in such as "Topic: I couldn't find…". Several "dont_know" cases are wrongly marked answered because of it.
  - One panel-hospital URL is printed twice.
  - `gpt-5-nano` sometimes says "couldn't find" even when the right passage is ranked first (the lawyer question failed 1 of 5 runs).
  - A GM asking about DGM figures is refused, although the two share a row. This is a product default the user may overrule.
  - `head_hr`, `hrbp` and `people_specialist` are outside the Travel and Medical audiences. This is a product decision, made in `access.py` `reference_audience`.

## Next steps

1. Ask the user what to fix next, or start the app if asked:
   - **API.** From `hr_desk/backend`, with `HR_DESK_PUBLIC_ORIGIN=http://localhost:3000`, `HR_DESK_DATA_ROOT=<hr_desk>/.runtime/local-hr-desk-data` and `DATABASE_URL=postgresql://hr_desk:hr_desk@127.0.0.1:5433/hr_desk` set, run `.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
   - **Web app.** From `hr_desk/web`, with `HR_DESK_API_TARGET=http://127.0.0.1:8000` set, run `node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3000`.
   - **Don't use `Start-HRDesk.ps1`.** Its `docker compose` stderr makes PowerShell abort.
2. Candidate fixes, in this order:
   1. The `fixed_reply` prefix refusal.
   2. Switching `plainto_tsquery` to an OR / `websearch_to_tsquery` match.
   3. Routing vague questions to `gpt-5-mini`.
   4. Extending the overview to travel and provident fund.
3. For each fix:
   1. Commit a failing test first.
   2. Fix it, then run `pytest -q evals`.
   3. Restart the API.
   4. Run `npx playwright test` in `hr_desk/e2e`.
   5. Compare against the previous `report/matrix` with a script like the ones used this session: keep the old `report/matrix` under `hr_desk/.runtime/` before rerunning.
   6. Check that `node lib/visibility-table.mjs` shows no leaks.

## Key constraints

- **Edit only inside `hr_desk/`.** This is the user's explicit instruction.
- **Never merge into `main`, never push.** Leave PRs for the user.
- **No AI attribution** in commits, PRs or code comments.
- **The local app requires the database named `hr_desk` on port 5433.** `activation.validate_deployment` enforces it. Never run migrations on shared data without reason. Use copy databases for experiments.
- **Git Bash quirks.**
  - Set `MSYS_NO_PATHCONV=1` for `docker exec` paths.
  - Generating Python source through `python - <<EOF` with backslash escapes has corrupted `\b` into backspace bytes twice. Use the Edit tool for regex code, and check for corruption with `grep -cP '\x08' <file>`.
- **The E2E suite rewrites part of the local database.** It creates and resets `e2e.*@stylo.example` accounts, clears `answer_cache`, and clears `request_budget` (except `login:account:*`). Test accounts' password: see `hr_desk/e2e/lib/personas.ts`.
- **Low memory.** Claude Code may stop background servers when the machine is low on memory. Report it; restart only on request.

## Memory pointers

- `~/.claude/projects/C--huSSNAIN-PROJECTS-RAG-Super/memory/hr-desk-only-scope.md`: the user wants work and edits only in `hr_desk/`.
- This session's scratchpad held the case designs, the trace audit (`trace_audit.md`) and the fix brief (`fix-brief.md`). It is temporary, so don't rely on it.

## Suggested skills

- `pstack:poteto-mode`: this session's working style. Use the Bug fix playbook for each defect.
- `superpowers:systematic-debugging` or `mattpocock-skills:diagnosing-bugs`: before changing code for a new defect.
- `superpowers:test-driven-development` or `pstack:tdd`: the failing test comes first, as for every fix here.
- `pstack:how`: when touching unfamiliar parts of `pipeline.py` or `retrieve.py`.
- `superpowers:verification-before-completion`: rerun the E2E suite and the leak check before claiming a fix.
- `run`: when the user asks to start or see the app.
