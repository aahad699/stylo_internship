# RAG audit

Read-only measurement of the indexed library on 24 November 2026. No application code, prompt, policy file, or database row was changed. Cache lookup, cache writes, traces, and request budgets were disabled for the run, so every question retrieved fresh.

Verified accounts used for role-dependent answers: Hina (general manager), Sara (intern), Insrah (HR). Other designations were passed through the same access filter without a signed-in account.

Retrieval followed the production path: access filter, effective-date filter, designation lookup where the app uses it, otherwise hybrid search with the production top-k (6, or up to 16 when a heading is named) and the 4-passage selection cap. A second search the app runs only to choose a refusal was not counted as a result the employee sees.

The question set is `questions.yaml` (100 questions, 76 with an expected passage). A question that lists several expected passages scores a hit only when all of them are inside the cutoff. That is why recall@1 is lower than recall@4 for fuel and daily allowance: those lookups correctly return two to four rows, and they cannot all be rank 1.

## 1. Corpus inventory

Six published documents. None is superseded, none has an end date, and none has zero passages. No duplicate or near-duplicate document.

| Document | Type | Effective | Status | Superseded by | Who can search it | Passages | Body bytes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Code of Conduct | docx | 2025-08-09 | published | none | everyone | 21 | 12,794 |
| Corrective Action Policy | docx | 2025-08-08 | published | none | everyone | 125 | 35,052 |
| Medical Insurance Policy | pdf | 2024-06-01 | published | none | management, store, and support roles listed on the policy, plus employee and HR | 32 | 16,807 |
| Provident Fund Loan Policy – Head Office | docx | 2026-05-01 | published | none | employee, manager, HR, executive | 30 | 7,928 |
| Travel Policy | pdf | 2025-04-15 | published | none | management and field-sales grades, plus employee and HR | 60 | 16,415 |
| User Guidelines - Advance Salary (HO - RSC) | docx | 2025-01-01 | published | none | everyone | 15 | 3,532 |

Flags:

- The medical file name is `Medical Takaful Policy 051124.pdf`. The indexed title is `Medical Insurance Policy`. "Takaful" does not appear as the title employees search.
- Travel is a scan. All 60 passages carry a page image. The words in the index are the curated reading of those pages, not a text layer from the PDF. No passage is image-only.
- Medical, corrective action, and advance salary include a few screenshot pages plus text. They are not image-only.
- The smallest passages are a 9-byte "Annexures" line, a 15-byte code-of-conduct cover line, and title-only chunks of a few dozen bytes. Those title chunks win several searches that should have opened a section.
- Corrective action is 125 passages because each violation row was stored on its own. That is the crowded document, not a duplicate file.
- Provident fund is in force (effective 1 May 2026). There is no future-dated policy and no superseded policy to retrieve. Questions about a 2023 or 2027 edition still match the current edition's date and renewal lines.

## 2. Chunking

283 passages. Size in bytes: minimum 9, median 166, 90th percentile 770, maximum 2,810. The splitter's limit is 3,200 bytes with a 400-byte overlap. Almost every section is already under 3,200 bytes, so the overlap never ran. Consecutive passages of the same heading share no prefix: 0 overlapping pairs, 0 bytes of duplicated tail. The 400-byte overlap is not what crowds the top results. The crowd is the number of short sibling rows, especially the 125 corrective-action rows.

Of 283 passages, 1 starts mid-sentence and 83 end without sentence punctuation. The unfinished endings are table rows and headings, not paragraphs cut in half. 100 table passages do not contain the word "grade". Those are corrective-action violation rows. One violation is one passage, and the column headers are not repeated on the row.

Checked rate rows:

| Passage | Bytes | Carries the row label | Currency or unit | 300 km condition |
| --- | --- | --- | --- | --- |
| Daily allowance rule, section 6 | 993 | no (it is the rule, not a grade row) | rupees mentioned | yes, in this same passage |
| General manager daily-allowance row | 110 | grade | rupees per day | no |
| All-levels 70cc fuel row | 157 | grade entitlement | Rs. per km and km/liter | no |
| General manager 1300cc fuel row | 157 | grade entitlement | Rs. per km and km/liter | no |
| General manager hotel row | in Annexure C | grade | hotel rate on the row | no |
| Medical plan rows | about 200–250 | plan and grades | per day on the benefit | not a travel rule |

The 300 km rule stays with the daily-allowance definition, not with the grade amount. The designation lookup for "daily allowance for me" retrieves both, and the general manager's answer included the amount and the 300 km condition. Asking "When does the 300 km rule apply?" retrieves the rule passage and then the answer path says it could not find it.

Tour-allowance rows are the weak table cut. The area sales manager row is the designation and two bare numbers. It does not repeat column headers or say per day. A reader of that passage alone cannot tell which number is the same-day rate.

Sampled passages used for the notes above include the code-of-conduct cover and values, the dress-code violation row, the investigation summary form, the travel claim form, the fuel and daily-allowance rows, the medical plan A row, a provident approval row, and the advance-salary portal section. Forms keep their field names in one passage (summary report 373 bytes, travel claim form 701 bytes).

## 3. Retrieval set

`questions.yaml` has 100 questions: plain wording, employee wording (takaful, DA, TA, lodging, mileage, absence), keywords, multi-topic, "for me" by designation, misspellings, questions with no policy, intern-blocked topics, superseded or future editions, and follow-ups that depend on history.

## 4. Metrics

Scored on the 76 questions that name an expected passage.

| | Recall@1 | Recall@4 | Recall@10 | MRR |
| --- | --- | --- | --- | --- |
| Overall (76) | 0.434 | 0.618 | 0.645 | 0.603 |

Recall@10 is only a little above recall@4 because production usually returns 6 passages. A passage at rank 7 to 10 is rarely inside that list. One passage (takaful eligibility, rank 6) was retrieved and then dropped by the 4-passage cap. The 12,000-byte evidence budget dropped none.

| Category | N | @1 | @4 | @10 |
| --- | --- | --- | --- | --- |
| Plain | 32 | 0.594 | 0.594 | 0.594 |
| Personal | 19 | 0.316 | 0.684 | 0.684 |
| Alias | 6 | 0.333 | 0.667 | 0.833 |
| Keyword | 5 | 0.600 | 0.600 | 0.600 |
| Misspelling | 6 | 0.500 | 0.500 | 0.500 |
| Multi-topic | 4 | 0.000 | 0.500 | 0.750 |
| Follow-up | 4 | 0.000 | 0.750 | 0.750 |

| Designation | N | @4 |
| --- | --- | --- |
| General manager | 26 | 0.731 |
| HR | 26 | 0.500 |
| Intern (questions they are allowed to answer) | 6 | 0.667 |
| People specialist | 2 | 1.000 |
| Manager | 4 | 0.500 |
| Officer | 2 | 0.500 |
| Executive | 3 | 0.333 |
| Assistant general manager, senior manager, deputy general manager, COO | 1 each | 1.000 |
| Area sales manager | 1 | 0.000 |

How the expected passage was found, among the 76:

| Channel | Questions |
| --- | --- |
| Vector only | 31 |
| Heading lookup (designation rates and named sections) | 26 |
| Vector and keyword | 16 |
| Keyword only | 1 |
| Neither | 2 |

Keyword search almost never finds a passage that vector search missed. Heading lookup is the path that actually returns fuel, hotel, and daily-allowance rows, and it is reliable for those rows.

False positives in the top 4: 5 questions brought in another policy or another topic (harassment mixed the code of conduct with corrective action; dress code pulled the code-of-conduct introduction; a tour-allowance question returned medical eligibility; "and for managers" returned store-crew expectations). No personal fuel or hotel question returned a neighbouring grade's row. Store manager and deputy general manager, who have no medical plan row, were not given another plan.

No-answer and wrong-edition questions that still retrieved passages: gym, work from home, bonus, the superseded medical policy, the 2027 travel policy, the 2020 code of conduct, a future advance-salary policy, and the intern advance-salary question. Casual leave, sick leave, gratuity, and the 2023 travel policy retrieved nothing in the employee's own search.

Latency, including the embedding call: median 720 ms, maximum 4,980 ms. The maximum was a question with no matching policy that still called the rewrite model.

## 5. Failure causes

29 of 76 expected-passage questions missed the top 4.

| Cause | Count |
| --- | --- |
| Ranking | 21 |
| Filter | 3 |
| Topic splitting or recognition | 2 |
| Vocabulary mismatch | 2 |
| Rewrite | 1 |

Examples:

1. Ranking. "health insurance for me" (general manager). Expected the plan A schedule. Returned panel-hospital claim steps, enrollment, the non-panel section, and the claim form.
2. Ranking. "medcal insurance for me". The typo was rewritten to "medical insurance", then the title chunk was returned instead of the plan row.
3. Ranking. "What does takaful cover for me?" Eligibility was rank 6 and then dropped by the 4-passage cap. The top passages were exclusions, maternity, and hospital care.
4. Ranking. "How does the provident fund loan work?" Expected eligibility and entitlement. Returned the 42-byte title passage.
5. Ranking. "PF loan" and "provident fund loan for me" did the same: the title passage, not the loan terms.
6. Ranking. "Who approves a provident fund loan?" and "How is a provident fund loan repaid?" also stopped on the title passage.
7. Ranking. "Which violations can lead to immediate dismissal?" The right section was found, but the particular row was rank 14. The top rows are other dismissal rows. The section is one violation per passage, so "the" answer has no single top passage.
8. Vocabulary. "provident fond loan" stayed misspelled and did not land on eligibility. The title and the form heading came back instead.
9. Vocabulary. "What should I do if I am unsure about the right course of action?" The CEO message and the responsibilities section were not in the list. The rewrite expanded the question and lost the heading.
10. Filter. "FIR" was expanded to "first investigation report". The summary-report form was not returned. A different corrective-action passage was.
11. Filter. "How do I open the ESS portal for advance salary?" Returned the later steps (enter details, track status), not the portal section that holds the address.
12. Topic recognition. "What is my tour allowance?" for an area sales manager entered the benefits overview. Top results were medical purpose, eligibility, and coverage. The tour row existed at rank 12.
13. Topic splitting. "medical insurance and provident fund" returned only provident title and form lines. The medical half did not land in the top 4.
14. Topic splitting. "fuel for me and hotel for me and daily allowance for me" returned the general manager fuel rows. Hotel was rank 6 and the daily-allowance amount rank 7, outside the top 4.
15. Rewrite. After a dress-code answer, "and for managers" kept both phrases and then retrieved store-crew expectations from the code of conduct, not the dress-code warning.

## 6. Answer check

30 questions were answered on the verified accounts. Cache was not used. Labels below are against the source passage and against what the employee would be shown. Two medical drafts were replaced by the output guard before display because they used the word "annexure"; those are counted as wrong, not as leaks.

| Label | Count |
| --- | --- |
| Correct | 20 |
| Wrong | 8 |
| Incomplete | 1 |
| Improvised | 0 |
| Leaked | 1 |

| Path | What it did on this sample |
| --- | --- |
| Deterministic | Travel rates, travel approval, the claim-form fields, the summary-report fields, the daily-allowance plus hotel split, the intern refusals, and the empty leave and old-policy answers. Finish reason is the deterministic return, not a model stop. |
| Model | CEO message, values, dress code, advance-salary steps, the dress-code plus travel split, and the provident questions. Finish reason `stop`. |
| Recalled | "What was my last question?" repeated the saved question and did not search again. |
| Guard after the model | Health insurance and maternity. The draft was on the medical policy. The employee was shown the refusal. |

The incomplete answer is the CEO message: it names the six values and tells the employee to ask HR, and it does not carry the source letter's wording. The leak is the intern advance-salary answer, which gave the portal steps. The eight wrong answers are the direct 300 km question, takaful, health insurance, maternity, panel-hospital claim, both provident questions, and "what about hotel" after a daily-allowance turn.

The general manager's "yes" and "mileage" after "TA" returned that designation's fuel rows and the 300 km condition. They did not return the senior-manager row.

## 7. Security and history

No indexed passage tripped the named-pay check. No passage contains an instruction to ignore rules, a system-prompt marker, or a delimiter that the injection scan looks for.

Intern against the general manager, on the employee's own search:

| Question | Intern retrieval | Intern answer | General manager |
| --- | --- | --- | --- |
| Hotel, fuel, daily allowance | no travel passage | refusal, before search | that grade's row |
| Health insurance | no medical passage | refusal | claim steps, not the plan row |
| Provident fund | no provident passage | not shown | no provident passage either, because a general manager is outside that audience |
| Advance salary | the request steps | the steps were answered | the same guide is visible |

History did not change the signed-in designation. "Mileage" and "yes" after "TA" stayed on the general manager and switched the topic to fuel. "Why not?" after an intern refusal repeated that refusal and did not open the travel policy. "What was my last question?" returned the previous question only. "What about hotel" changed the topic away from daily allowance. "And for managers" did not switch the viewer from HR to a manager account; it did retrieve the wrong section.

## Recommended fixes

1. Access on advance salary. Evidence: the guide is audience "everyone", the intern's own search returns the steps, and the answer path delivered them. The title ban does not run once the passage's role list includes intern. Mark that guide the way provident is marked, or apply the intern ban even when the role list is broad.
2. Aliases for medical. Evidence: "health insurance", "medical insurance", "takaful", and "medcal insurance" do not open the plan schedule. Map those phrases onto the benefit schedule the way "DA" is mapped onto daily allowance.
3. Do not let a title-only chunk outrank the section. Evidence: provident questions and several medical questions return the cover line. Drop passages of only a few dozen bytes, or do not index the bare title.
4. Topic list for tour allowance. Evidence: "what is my tour allowance?" returned medical eligibility, with the area-sales row at rank 12. Recognize tour allowance as its own travel lookup.
5. Ranking of split tables. Evidence: immediate dismissal's useful row is rank 14 because each violation is its own passage. Retrieve the sibling rows of that heading together, as fuel rows already are.
6. Repeat column headers on the tour table. Evidence: the area and regional rows are a designation and two numbers, with no unit.
7. Keep the 300 km rule attached when the question names the rule, not only when the question asks for the daily amount. Evidence: the direct question retrieved the passage and the answer was "I couldn't find this."
8. Stop refusing a draft only because it cites an annexure by name, or teach the model to describe the form without that word. Evidence: health insurance and maternity were refused after a draft that pointed at the claim form and the benefit schedule.
9. Multi-topic cap. Evidence: three travel questions in one message kept fuel and pushed hotel and daily allowance past the top 4. The 12,000-byte budget was not the limit. The per-topic passage cap was.

Not recommended from this run: changing the 12,000-byte budget, or adding keyword weight. Keyword-only recovery happened once.

## Not measured

- Browser rendering of answers or page images.
- Cache hits. The cache was bypassed so this run would measure retrieval rather than a stored answer.
- A second or third answer for the same question, so variation across runs is unknown.
- Whether a live model stops because the answer reached the token limit.
- Signed-in sessions for designations other than general manager, intern, and HR. Those other grades used the same filter the account would use.
- A real superseded policy. None is indexed, so the superseded questions only show what the current documents do when the question names another year.

## Gate diagnostics

The relevance gate is the check that runs after candidates are scored and before they are ranked. A passage is kept when any of these is true, in this order:

1. Keyword score at least 0.67.
2. Vector score at least 0.55.
3. Vector score at least 0.25 and keyword score at least 0.34.
4. A one-character typo match and keyword score at least 0.34.

This section scores the retriever itself. It does not use the designation lookup that answers "for me" fuel and hotel questions before search. The typed question is the search string. Audience and effective date are the production filters. Candidates are the vector top 20 plus any heading, clause, or page hits, which is the list the gate sees.

### Medical Insurance Policy for me

Asked as a general manager. The retriever returned 25 passages, all from Medical Insurance Policy. The gate kept all 25. None were dropped. The plan schedule is in that list, at fused rank 9, so a later top-4 cut removes it. The gate does not.

The title passage is the only one whose keyword score was forced to 1 because its heading matches the phrase "medical insurance". Every other row already scored 1 from the overlap formula, because the document title sits in the text that is scored.

| Rank | Passage | Heading | Vector | Keyword | Gate | Dropped |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | doc-922ff7991e7a9615:0 | Medical Insurance Policy | 0.761 | 1.00 | keyword ≥ 0.67 | no |
| 2 | doc-922ff7991e7a9615:31 | Document header | 0.659 | 1.00 | keyword ≥ 0.67 | no |
| 3 | doc-922ff7991e7a9615:1 | 1. Purpose | 0.653 | 1.00 | keyword ≥ 0.67 | no |
| 4 | doc-922ff7991e7a9615:18 | Annexures | 0.640 | 1.00 | keyword ≥ 0.67 | no |
| 5 | doc-922ff7991e7a9615:5 | 3.1.1. Hospital Care Benefit | 0.635 | 1.00 | keyword ≥ 0.67 | no |
| 6 | doc-922ff7991e7a9615:3 | 3. Coverage | 0.631 | 1.00 | keyword ≥ 0.67 | no |
| 7 | doc-922ff7991e7a9615:8 | 5. Procedure For Reimbursement | 0.631 | 1.00 | keyword ≥ 0.67 | no |
| 8 | doc-922ff7991e7a9615:2 | 2. Eligibility | 0.627 | 1.00 | keyword ≥ 0.67 | no |
| 9 | doc-922ff7991e7a9615:21 | Annexure B benefit schedule | 0.623 | 1.00 | keyword ≥ 0.67 | no |
| 10 | doc-922ff7991e7a9615:9 | 5.1. Panel hospital claim | 0.621 | 1.00 | keyword ≥ 0.67 | no |
| 11 | doc-922ff7991e7a9615:7 | 4. Exclusions | 0.612 | 1.00 | keyword ≥ 0.67 | no |
| 12 | doc-922ff7991e7a9615:20 | In-Patient Benefit Entitlement Schedule | 0.608 | 1.00 | keyword ≥ 0.67 | no |
| 13 | doc-922ff7991e7a9615:11 | 5.2. Non-panel claim | 0.606 | 1.00 | keyword ≥ 0.67 | no |
| 14 | doc-922ff7991e7a9615:6 | 3.1.2. Maternity Benefit | 0.604 | 1.00 | keyword ≥ 0.67 | no |
| 15 | doc-922ff7991e7a9615:26 | Annexure C exclusions | 0.598 | 1.00 | keyword ≥ 0.67 | no |
| 16 | doc-922ff7991e7a9615:16 | 10. Policy Attachments | 0.592 | 1.00 | keyword ≥ 0.67 | no |
| 17 | doc-922ff7991e7a9615:10 | 5.2. Non-panel claim, continued | 0.587 | 1.00 | keyword ≥ 0.67 | no |
| 18 | doc-922ff7991e7a9615:4 | 3.1. In-Patient Medical Support | 0.586 | 1.00 | keyword ≥ 0.67 | no |
| 19 | doc-922ff7991e7a9615:27 | Annexure C exclusions, continued | 0.581 | 1.00 | keyword ≥ 0.67 | no |
| 20 | doc-922ff7991e7a9615:17 | 11. Policy Effective Date | 0.576 | 1.00 | keyword ≥ 0.67 | no |
| 21 | doc-922ff7991e7a9615:14 | 8. Termination of Coverage | 0.571 | 1.00 | keyword ≥ 0.67 | no |
| 22 | doc-922ff7991e7a9615:12 | 6. Enrollment of New Employee | 0.564 | 1.00 | keyword ≥ 0.67 | no |
| 23 | doc-922ff7991e7a9615:19 | Annexure A: In-Patient Medical Support | 0.560 | 1.00 | keyword ≥ 0.67 | no |
| 24 | doc-922ff7991e7a9615:15 | 9. Policy Renewal and Review | 0.552 | 1.00 | keyword ≥ 0.67 | no |
| 25 | doc-922ff7991e7a9615:13 | 7. Change in Family Status | 0.551 | 1.00 | keyword ≥ 0.67 | no |

### How the keyword score is computed

The query is lowercased and split into letters and digits. A token is dropped when it is in the stop list or is a single character. The stop list includes "for", "me", and "policy", so those three words are not scored and they are not in the denominator.

For "Medical Insurance Policy for me" the tokens that remain are `medical` and `insurance`. The denominator is the number of unique remaining tokens, which is 2. Each of those tokens counts once, even if it were repeated.

A token hits when the passage text contains that exact word, or a word of length at least 5 that is one edit away and within one character of the same length. The score is hits divided by the denominator. One of the two words gives 0.5. Both give 1.

"Insurance" and "takaful" do not match. They are different words, and the lengths differ by 2, so the one-edit rule does not apply. A passage that says takaful and never says insurance gets no credit for that token. On this query that does not change the score, because every medical passage is labeled with the title "Medical Insurance Policy", and that label is part of the scored text. Both tokens hit, so the keyword score is 1 before the gate runs.

### Threshold sweep

Same 100 questions, 76 with an expected passage. After the gate, passages are ordered by the fused score and recall@4 is whether every expected passage is inside the first 4. A false positive is a top-4 passage from a different document, or any top-4 passage when the question has no document in the library. The vector floor of the third gate stays 0.25. The typo branch uses the same keyword floor as that third gate.

Current setting: keyword 0.67, vector 0.55, keyword floor 0.34. Recall@4 is 0.592 (45 of 76). False positives: 20 questions.

| Keyword high | Vector high | Keyword floor | Recall@4 | False-positive questions |
| --- | --- | --- | --- | --- |
| 0.50 | 0.40 | 0.20 | 0.605 | 27 |
| 0.50 | 0.40 | 0.34 | 0.605 | 25 |
| 0.50 | 0.40 | 0.50 | 0.605 | 25 |
| 0.50 | 0.55 | 0.20 | 0.605 | 26 |
| 0.50 | 0.55 | 0.34 | 0.605 | 23 |
| 0.50 | 0.55 | 0.50 | 0.605 | 23 |
| 0.50 | 0.70 | 0.20 | 0.605 | 26 |
| 0.50 | 0.70 | 0.34 | 0.605 | 22 |
| 0.50 | 0.70 | 0.50 | 0.605 | 22 |
| 0.67 | 0.40 | 0.20 | 0.592 | 24 |
| 0.67 | 0.40 | 0.34 | 0.592 | 22 |
| 0.67 | 0.40 | 0.50 | 0.592 | 22 |
| 0.67 | 0.55 | 0.20 | 0.592 | 23 |
| 0.67 | 0.55 | 0.34 | 0.592 | 20 |
| 0.67 | 0.55 | 0.50 | 0.592 | 20 |
| 0.67 | 0.70 | 0.20 | 0.592 | 23 |
| 0.67 | 0.70 | 0.34 | 0.592 | 19 |
| 0.67 | 0.70 | 0.50 | 0.592 | 19 |
| 0.80 | 0.40 | 0.20 | 0.592 | 24 |
| 0.80 | 0.40 | 0.34 | 0.592 | 22 |
| 0.80 | 0.40 | 0.50 | 0.592 | 22 |
| 0.80 | 0.55 | 0.20 | 0.592 | 23 |
| 0.80 | 0.55 | 0.34 | 0.592 | 20 |
| 0.80 | 0.55 | 0.50 | 0.592 | 20 |
| 0.80 | 0.70 | 0.20 | 0.592 | 23 |
| 0.80 | 0.70 | 0.34 | 0.592 | 19 |
| 0.80 | 0.70 | 0.50 | 0.592 | 19 |
| no gate |  |  | 0.645 | 37 |

Recall moves only when the high keyword cutoff drops from 0.67 to 0.50: 45 hits become 46. Raising the vector cutoff from 0.55 to 0.70 does not change recall at the current keyword cutoff, and it removes one false-positive question. Removing the gate entirely adds 4 hits (49 of 76, recall@4 0.645) and raises false-positive questions from 20 to 37.

### Expected passages the gate drops

Of the expected passages that were inside the candidate list, the current gate drops 15 of 101, which is 14.9 percent. The other expected passages were never in the candidate list, so the gate did not see them.

Passages the gate removed even though search had already retrieved them include the gifts section (vector 0.381, keyword 0), the CEO message on the "unsure" question (vector 0.172, keyword 0.667), the summary-report form for "FIR" (vector 0.302, keyword 0), the general-manager daily-allowance row for "DA for me" (vector 0.182, keyword 0), the general-manager hotel row for "lodging" (vector 0.357, keyword 0), the plan schedule for "takaful" (vector 0.341, keyword 0), and the hotel row inside the three-topic travel question (vector 0.397, keyword 0.25). Those keyword scores are 0 when the question's remaining tokens do not occur in the passage. The token "da" is not the words "daily allowance", and "takaful" does not occur on the plan row.
