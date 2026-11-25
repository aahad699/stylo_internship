import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { askThread } from "../lib/ask";
import { apiAs } from "../lib/personas";
import { containsForbidden, missingExpectedFact, norm } from "../lib/matrix-rules.mjs";

interface Case {
  id: string;
  category: string;
  designation: string;
  question: string;
  history?: string[];
  expect: string;
  accept_also?: string[];
  must_contain_any?: string[];
  must_not_contain?: string[];
  must_not_match?: string[];
  source?: string;
  why?: string;
  confidence?: string;
}

const files = ["cases/policy-cases.json", "cases/trace-cases.json", "cases/overview-regressions.json"];
const cases: (Case & { file: string })[] = files.flatMap((file) =>
  (JSON.parse(readFileSync(file, "utf8")) as Case[]).map((item) => ({ ...item, file })),
);

// "1,500" and "1500" are the same figure; compare without separators or case.

mkdirSync("report/matrix", { recursive: true });

for (const item of cases) {
  test(`${item.category} ${item.id} [${item.designation}]`, async () => {
    test.info().annotations.push({ type: "question", description: item.question });
    const api = await apiAs(item.designation);
    const turns = await askThread(api, [...(item.history ?? []), item.question]);
    await api.dispose();
    const last = turns.at(-1)!;
    const answer = norm(last.answer);
    const allowed = [item.expect, ...(item.accept_also ?? [])];
    const missing = missingExpectedFact(last.outcome, answer, item.must_contain_any);
    const leaked = [
      ...(item.must_not_contain ?? []).filter((bad) => containsForbidden(answer, bad)),
      ...(item.must_not_match ?? []).filter((pattern) => new RegExp(pattern, "i").test(last.answer)),
    ];
    const verdict = {
      ...item,
      turns,
      outcomeOk: allowed.includes(last.outcome),
      factOk: !missing,
      leaked,
      pass: allowed.includes(last.outcome) && !missing && leaked.length === 0,
    };
    writeFileSync(`report/matrix/${item.id}.json`, JSON.stringify(verdict, null, 2));

    expect.soft(allowed, `outcome ${last.outcome} for "${item.question}"\nanswer: ${last.answer}`).toContain(last.outcome);
    expect.soft(missing, `expected one of ${JSON.stringify(item.must_contain_any)}\nanswer: ${last.answer}`).toBe(false);
    expect.soft(leaked, `forbidden content in answer: ${last.answer}`).toEqual([]);
  });
}
