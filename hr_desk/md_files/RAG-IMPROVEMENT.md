# Retrieval improvement

Branch `retrieval-quality`. The previous chunk index is in Postgres table `policy_chunks_v1` (283 rows). The live table has 277 rows. Authorization, audience filters, and the effective-date filter were not changed. Advance-salary access was not changed.

The question split is frozen in `backend/evals/rag_audit/split.json` (seed `retrieval-split-2026-11-24`): 67 tuning, 33 holdout, stratified by category and designation. Aliases and the rerank floor were not fit on the holdout. No real employee questions were added. The 30 extra answer questions were written for this check.

## Targets

| Target | Result |
| --- | --- |
| Holdout recall@4 at least 0.85 | Missed. Holdout recall@4 is 0.625. |
| Answers correct at least 90% | Missed. 48 of 60 answers are correct (80%). |
| No new false positives on no-answer questions | Met for the original no-answer set. Gym and work-from-home still retrieve passages, as they did in the audit. Bonus no longer does. |
| Intern and role-boundary results unchanged | Missed in one place. Intern medical now says it could not find a policy instead of the previous role refusal. No medical text is shown. The intern advance-salary answer is still produced, because that access was left unchanged. |

Overall recall@4 on the 76 scored questions moved from 0.618 to 0.697. Mean reciprocal rank moved from 0.603 to 0.579. More questions now place the expected passage in the top four, but the first hit is less often the single best passage, so MRR is lower.

## What changed, one step at a time

Retrieval was re-run after each stage. Answer counts were taken once, at the end. The first code run crashed on a list-comprehension bug and was discarded. The numbers below are the successful runs.

| Stage | Tuning recall@4 | Tuning MRR | Tuning false positives | Holdout recall@4 | Holdout MRR | Holdout false positives |
| --- | --- | --- | --- | --- | --- | --- |
| Audit baseline, full set, not split | 0.618 overall | 0.603 overall | 5 overall | — | — | — |
| Rank fusion, title removed from the keyword score, alias expansion, lexical rerank. Old embeddings. | 0.692 | 0.547 | 9 | 0.500 | 0.443 | 4 |
| Re-embed on the copied index: tiny headings merged, alias prefix, tour row labels, 300 km link in the index text | 0.673 | 0.538 | 9 | 0.542 | 0.464 | 6 |
| Tour allowance lookup, and a personal medical lookup for health insurance | 0.731 | 0.593 | 7 | 0.625 | 0.548 | 5 |

The last row removes one tuning hit that the medical lookup had counted for HR. That lookup was returning another grade's plan, so HR is no longer given every plan row. Holdout was not affected.

## Retrieval after the correction

Baseline is the 24 November audit on all 76 scored questions. After is the frozen split. Overall after is the two splits combined.

| Set | Recall@1 | Recall@4 | Recall@10 | MRR |
| --- | --- | --- | --- | --- |
| Baseline, all scored | 0.434 | 0.618 | 0.645 | 0.603 |
| After, tuning (52 scored) | 0.500 | 0.731 | 0.750 | 0.593 |
| After, holdout (24 scored) | 0.458 | 0.625 | 0.667 | 0.548 |
| After, both splits | 0.487 | 0.697 | 0.724 | 0.579 |

### By category

Baseline n is the full audit. After n is tuning / holdout.

| Category | Baseline @4 | Tuning @4 | Holdout @4 | Tuning MRR | Holdout MRR |
| --- | --- | --- | --- | --- | --- |
| plain | 0.594 (32) | 0.682 (22) | 0.500 (10) | 0.663 | 0.400 |
| personal | 0.684 (19) | 0.846 (13) | 0.833 (6) | 0.622 | 0.667 |
| alias | 0.667 (6) | 0.800 (5) | 1.000 (1) | 0.550 | 1.000 |
| keyword | 0.600 (5) | 0.667 (3) | 1.000 (2) | 0.667 | 1.000 |
| misspelling | 0.500 (6) | 0.333 (3) | 0.333 (3) | 0.400 | 0.333 |
| multi | 0.500 (4) | 1.000 (2) | 0.500 (2) | 0.375 | 0.571 |
| follow-up | 0.750 (4) | 0.750 (4) | — | 0.375 | — |

### By designation

Cells with one question move a lot. Read them with the sample size.

| Designation | Baseline @4 | Tuning @4 (n) | Holdout @4 (n) |
| --- | --- | --- | --- |
| general_manager | 0.731 (26) | 0.895 (19) | 0.429 (7) |
| hr | 0.500 (26) | 0.529 (17) | 0.667 (9) |
| intern, questions with an expected passage | 0.667 (6) | 0.750 (4) | 0.500 (2) |
| manager | 0.500 (4) | 0.500 (2) | 1.000 (2) |
| officer | 0.500 (2) | 1.000 (1) | 1.000 (1) |
| executive | 0.333 (3) | 1.000 (1) | 0.500 (2) |
| employee | 0.500 (2) | 0.000 (2) | — |
| area_sales_manager | 0.000 (1) | 1.000 (1) | — |
| people_specialist | 1.000 (2) | 1.000 (2) | — |
| assistant_general_manager | 1.000 (1) | 1.000 (1) | — |
| senior_manager | 1.000 (1) | 1.000 (1) | — |
| coo_business | 1.000 (1) | 1.000 (1) | — |
| deputy_general_manager | 1.000 (1) | — | 1.000 (1) |

## Answers

The same answer questions as the audit, plus `fu_yes` from the retrieval run, plus 30 authored questions. Not employee questions.

| | Correct | Wrong | Incomplete | Improvised | Leaked |
| --- | --- | --- | --- | --- | --- |
| Audit, 30 questions | 20 | 8 | 1 | 0 | 1 |
| This run, 60 questions | 48 | 7 | 4 | 0 | 1 |

Wrong: `coc_ceo`, `med_takaful`, `med_panel`, `new_takaful_hr`, `new_pf_short`, `new_dress`, `new_ceo`.

Incomplete: `cor_dress` (the reply does not say "warning"), `pf_how` (the sentence is cut), `new_values` (Respect is missing), `new_store_medical` (a general coverage sentence, not another grade's plan row).

Leaked: `intern_advance`. The intern still receives the advance-salary steps. That access was left as it was.

`med_health` and `med_maternity` are kept. The word annexure is removed from the reply. The canary and the other banned phrases still refuse.

`tr_300` answers from the daily-allowance rule. `fu_hotel` and `new_hotel_follow` keep the general manager travel context after a daily-allowance turn.

Gym, work from home, bonus, and the authored leave question return the not-found reply. The 2023 travel question and the 2027 medical question use that fallback. They do not call the current edition the named year. They also do not state the current effective date, which the other allowed option would have done.

## Latency

Retrieval run, including the embedding call: median 850 ms, maximum 12,343 ms. The audit median was 720 ms and the maximum was 4,980 ms. The long tail is a model rewrite, not the reranker.

## Reranker

The found-or-not-found threshold was not raised. On the tuning set, real hits score as low as 0.036 and the gym question scores 0.038. Any threshold that drops gym, work-from-home, or the wrong-edition retrievals also drops real dress-code and travel hits. The floor stays 0. Below a raised floor the pipeline would have skipped the model; with the floor at 0 it does not.

| Option | What it would add | Decision |
| --- | --- | --- |
| Lexical rerank of the top 20 fused rows | No new package and no extra model call. It penalises a passage under 80 bytes and adds a small heading and body overlap. This is what shipped. | Used. |
| Local cross-encoder | A new package and a model download. Typical CPU cost is tens to a few hundred milliseconds. No per-query API fee. | Not added. |
| Small model scores the 20 candidates | Uses the existing API key, so it is a paid call. Added latency is likely 1 to 3 seconds against a 720 ms median, plus the token cost of one batch or twenty short scores. | Not added. Say if you want this tried. |

## Aliases

Stored in `backend/data/retrieval_aliases.json`. Search keeps the original words and appends the rest of the matching group. The displayed passage body does not include this prefix. The embedding and the index context do.

| Phrase | Means | Source |
| --- | --- | --- |
| medical insurance | medical insurance | Indexed title |
| health insurance | medical insurance | Judgment |
| medical cover | medical insurance | Judgment |
| hospitalization | medical insurance | Judgment; the policy headings use hospitalization and in-patient |
| takaful | medical insurance | Source file name Medical Takaful Policy |
| daily allowance | daily allowance | Heading 6. Daily Allowance |
| da | daily allowance | Judgment; the heading spells the words out |
| travel approval | travel approval | Heading Authorization of Travel |
| ta | travel approval, and also travel allowance | Judgment. Topic detection uses travel approval only, so "who approves my TA" stays an approval question. Search also appends travel allowance. |
| travel allowance | travel allowance | Judgment |
| daily tour allowance, tour allowance | daily tour allowance | Heading 7. Daily Tour Allowance for Field-Based Employees |
| hotel | hotel | Heading Hotel Entitlement |
| lodging | hotel | Judgment |
| fuel | fuel | Heading Annexure D: Fuel Entitlement List |
| mileage | fuel | The fuel table's mileage column |
| first investigation report, fir | first investigation report | Judgment. The form heading is Summary Report. |
| provident fund | provident fund | Indexed title |
| pf | provident fund | Judgment |

Absence and leave were not given an alias. The corpus has no leave policy. A leave question still gets the not-found reply.

## Index

Six title or "Annexures" passages were merged into the next real section and then removed: `doc-14908bcf9bf82ed2:0`, `doc-6a4e4417299da190:0`, `doc-6a4e4417299da190:85`, `doc-922ff7991e7a9615:0`, `doc-922ff7991e7a9615:18`, `doc-e50809e1dd67ce54:0`. None of those ids were an expected passage. Page-number passages were left in place.

Tour rows now start with "Daily tour allowance rates." The two numbers are still unlabeled, because the source row has no column headings. Sibling rows share the heading, and a table hit brings the other rows of that heading back with the best row first. Daily-allowance, fuel, and hotel embeddings carry a 300 km link line. The displayed row body does not. When those rows are selected, the rule passage is attached for the answer.

Restore the previous index with:

```sql
DELETE FROM policy_chunks c
WHERE NOT EXISTS (SELECT 1 FROM policy_chunks_v1 v WHERE v.id = c.id);
INSERT INTO policy_chunks
SELECT v.* FROM policy_chunks_v1 v
WHERE NOT EXISTS (SELECT 1 FROM policy_chunks c WHERE c.id = v.id);
UPDATE policy_chunks c
SET body = v.body, embed_text = v.embed_text, embedding = v.embedding,
    heading = v.heading, section_label = v.section_label, heading_path = v.heading_path
FROM policy_chunks_v1 v
WHERE c.id = v.id;
```

Do not insert into `search_text`. It is generated from the body and the section label.

## Questions that got worse

Retrieval, compared with the audit hit@4:

| Question | Why |
| --- | --- |
| What if my flight is cancelled? | The cancellation passage used to match because the document title was inside the keyword score. The title is no longer scored. "Cancelled" is not a one-edit match for "cancellations", so the passage falls out of the top four. |
| advance salry | Same title effect. The misspelling no longer inherits a match from the document title, and it does not outrank the other passages. |

Answer path, even where retrieval did not lose the passage:

| Question | Why |
| --- | --- |
| message from ceo | The passage is retrieved. The answer is the not-found reply, so this went from an incomplete answer to no answer. |
| How do I claim at a panel hospital? | The reply leads with the non-panel claim. The audit did not answer this. This is a new wrong answer. |
| health insurance for me, asked as the intern | No medical text is shown. The wording changed from the role refusal to the not-found reply. |

## Not verified

- The signed-in web screen. The checks called the pipeline in process.
- A restarted API process. The running server, if one is up, is still the previous code until it is restarted.
- Real employee questions. None were added.
- The cross-encoder and the paid candidate scorer. They were not run.
- Whether every authored expected passage id is the only acceptable passage. Answer labels used the reply, not those ids.
- Cache hits. The measurement disabled cache writes and cache reads.
- Provider truncation beyond the one cut provident-fund sentence.
- The exact added milliseconds of the lexical rerank alone. The median rose from 720 ms to 850 ms for the whole retrieval path.
