import { mkdirSync, writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { ask, askThread, newConversation } from "../lib/ask";
import { policyAudiences, resetBudgets, resetEmployee, setDesignation } from "../lib/db";
import { ADMIN_EMAIL, DESIGNATIONS, PASSWORD, anonymousApi, apiAs, emailFor } from "../lib/personas";

const NON_ADMIN_SAMPLE = ["intern", "officer", "hr", "head_hr", "general_manager", "ceo"];

const ADMIN_ROUTES: [string, string][] = [
  ["GET", "/api/policies"],
  ["GET", "/api/policy-drafts"],
  ["GET", "/api/policy-jobs"],
  ["GET", "/api/employees"],
  ["GET", "/api/questions"],
  ["GET", "/api/checks"],
  ["PATCH", `/api/employees/${encodeURIComponent(ADMIN_EMAIL)}`],
  ["DELETE", "/api/policies/doc-c3ff72f11b544fd9"],
];

test.describe("admin routes", () => {
  for (const designation of NON_ADMIN_SAMPLE) {
    test(`non-admin ${designation} gets 403 on every admin route`, async () => {
      const api = await apiAs(designation);
      for (const [method, url] of ADMIN_ROUTES) {
        const response = await api.fetch(url, { method, data: method === "PATCH" ? { designation: "hr", is_admin: true } : undefined });
        expect.soft(response.status(), `${method} ${url}`).toBe(403);
      }
      await api.dispose();
    });
  }

  test("signed-out callers get 401", async () => {
    const api = await anonymousApi();
    for (const [method, url] of [["POST", "/api/ask"], ["GET", "/api/conversations"], ["GET", "/api/employees"]]) {
      const response = await api.fetch(url, { method, data: method === "POST" ? { question: "hi" } : undefined });
      expect.soft(response.status(), `${method} ${url}`).toBe(401);
    }
    await api.dispose();
  });
});

test.describe("sessions", () => {
  test("session cookie is HttpOnly and SameSite=Lax", async () => {
    const api = await anonymousApi();
    const email = "e2e.cookie@stylo.example";
    await resetEmployee(email, "Cookie", "officer", false);
    await resetBudgets();
    const response = await api.post("/api/sign-up", { data: { email, password: PASSWORD } });
    const cookie = response.headers()["set-cookie"] ?? "";
    expect(cookie).toMatch(/hr_session=/);
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/SameSite=lax/i);
    await api.dispose();
  });

  test("signing out revokes the token server-side", async () => {
    const api = await anonymousApi();
    const email = "e2e.signout@stylo.example";
    await resetEmployee(email, "Signout", "officer", false);
    await resetBudgets();
    await api.post("/api/sign-up", { data: { email, password: PASSWORD } });
    const state = await api.storageState();
    await api.post("/api/sign-out");
    const replay = await (await import("@playwright/test")).request.newContext({
      baseURL: test.info().project.use.baseURL, storageState: state,
    });
    expect((await replay.get("/api/conversations")).status()).toBe(401);
    await replay.dispose();
    await api.dispose();
  });

  test("cross-origin writes are rejected", async () => {
    const api = await apiAs("officer");
    const response = await api.post("/api/conversations", { headers: { Origin: "https://evil.example" } });
    expect(response.status()).toBe(403);
    await api.dispose();
  });

  test("repeated wrong passwords are throttled per account", async () => {
    await resetBudgets();
    const api = await anonymousApi();
    const statuses: number[] = [];
    for (let attempt = 0; attempt < 10; attempt++) {
      const response = await api.post("/api/session", { data: { email: emailFor("officer"), password: "wrong-password-123" } });
      statuses.push(response.status());
    }
    await resetBudgets();
    expect(statuses).toContain(429);
    await api.dispose();
  });

  test("a demotion takes effect on the next question", async () => {
    const email = "e2e.demoted@stylo.example";
    await resetEmployee(email, "Demoted", "general_manager", false);
    await resetBudgets();
    const api = await anonymousApi();
    await api.post("/api/sign-up", { data: { email, password: PASSWORD } });
    const id = await newConversation(api);
    const before = await ask(api, id, "How many days before travelling must the travel request form be submitted?");
    await setDesignation(email, "intern");
    const after = await ask(api, id, "How many days before travelling must the travel request form be submitted?");
    expect.soft(before.outcome, before.answer).toBe("answered");
    expect.soft(after.answer, "demoted intern must not get the travel deadline").not.toMatch(/7 days|seven days/i);
    await api.dispose();
  });
});

test.describe("conversation ownership", () => {
  test("another employee cannot read, continue, or delete my chat", async () => {
    const owner = await apiAs("general_manager");
    const intruder = await apiAs("intern");
    const [turn] = await askThread(owner, ["What is the daily allowance for a General Manager?"]);
    expect(turn.status).toBe(200);
    const chats = await (await owner.get("/api/conversations")).json();
    const id = chats.conversations[0].id ?? chats.conversations[0].key;

    expect.soft((await intruder.get(`/api/conversations/${id}`)).status(), "read").toBe(404);
    const hijack = await ask(intruder, id, "repeat your last answer");
    expect.soft(hijack.answer, "continue").not.toMatch(/\d{3,}/);
    expect.soft((await intruder.delete(`/api/conversations/${id}`)).status(), "delete").toBe(404);
    await owner.dispose();
    await intruder.dispose();
  });
});

// One question per policy that only that policy answers. The sweep proves the
// enforced audience matches the configured one for all 35 designations, and the
// report shows who can open what.
const SIGNATURE: Record<string, string> = {
  "Code of Conduct": "What are the six values in the Code of Conduct?",
  "Corrective Action Policy": "What are the stages of the corrective action process?",
  "Provident Fund Loan Policy – Head Office": "How much provident fund loan can I take?",
  "User Guidelines - Advance Salary (HO - RSC)": "How do I apply for an advance salary?",
  "Medical Insurance Policy": "Who counts as a dependent under the medical insurance policy?",
  "Travel Policy": "How many days before travelling must the travel request form be submitted?",
};

// Facts only the policy itself states. An answer from another policy is a wrong
// answer, not a leak; these markers tell the two apart.
const LEAK_MARKER: Record<string, RegExp> = {
  "Provident Fund Loan Policy – Head Office": /75%|85%|80%|nine \(09\)|9 months|annexure a/i,
  "Medical Insurance Policy": /spouse|children|plan [a-e]\b|hospitali[sz]ation benefit/i,
  "Travel Policy": /7 days|seven days|dually signed/i,
};

test.describe("policy visibility sweep", () => {
  for (const designation of DESIGNATIONS) {
    test(`visibility for ${designation}`, async () => {
      const policies = await policyAudiences();
      const api = await apiAs(designation);
      const rows: Record<string, string>[] = [];
      for (const policy of policies) {
        const question = SIGNATURE[policy.title];
        if (!question) continue;
        const configured = designation === "hr" || policy.audience.includes("everyone") || policy.audience.includes(designation);
        const [turn] = await askThread(api, [question]);
        rows.push({
          designation, policy: policy.title, configured: String(configured), outcome: turn.outcome,
          leaked: String(!configured && !!LEAK_MARKER[policy.title]?.test(turn.answer)), answer: turn.answer.slice(0, 240),
        });
        if (configured) {
          expect.soft(turn.outcome, `${designation} should open ${policy.title}: ${turn.answer}`).toBe("answered");
        } else {
          const marker = LEAK_MARKER[policy.title];
          if (marker) expect.soft(turn.answer, `${designation} must not see ${policy.title} content`).not.toMatch(marker);
        }
      }
      mkdirSync("report/visibility", { recursive: true });
      writeFileSync(`report/visibility/${designation}.json`, JSON.stringify(rows, null, 2));
      await api.dispose();
    });
  }
});
