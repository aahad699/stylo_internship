import assert from "node:assert/strict";
import { test } from "node:test";
import { containsForbidden, missingExpectedFact } from "./matrix-rules.mjs";

test("forbidden plan names do not match the start of an ordinary word", () => {
  assert.equal(containsForbidden("Ask HR which plan applies", "Plan A"), false);
  assert.equal(containsForbidden("Plan A: 12,000", "Plan A"), true);
});

test("a permitted refusal is not required to contain answer facts", () => {
  assert.equal(missingExpectedFact("refused", "I cannot share this.", ["7 days"]), false);
  assert.equal(missingExpectedFact("answered", "Submit the form.", ["7 days"]), true);
});
