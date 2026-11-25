import { mkdirSync } from "node:fs";
import { request } from "@playwright/test";
import { BASE_URL } from "../playwright.config";
import { clearAnswerCache, resetBudgets, resetEmployee } from "./db";
import { ADMIN_EMAIL, DESIGNATIONS, PASSWORD, emailFor, statePath } from "./personas";

async function signUp(email: string, path: string) {
  await resetBudgets();
  const api = await request.newContext({ baseURL: BASE_URL, extraHTTPHeaders: { Origin: BASE_URL } });
  const response = await api.post("/api/sign-up", { data: { email, password: PASSWORD } });
  if (!response.ok()) {
    throw new Error(`sign-up ${email} -> ${response.status()} ${await response.text()}`);
  }
  await api.storageState({ path });
  await api.dispose();
}

export default async function globalSetup() {
  const health = await (await request.newContext({ baseURL: BASE_URL })).get("/api/health");
  if (!health.ok()) throw new Error(`HR Desk is not running at ${BASE_URL}. Start it with Start-HRDesk.ps1.`);
  mkdirSync(".auth", { recursive: true });
  for (const designation of DESIGNATIONS) {
    const email = emailFor(designation);
    await resetEmployee(email, `E2E ${designation}`, designation, false);
    await signUp(email, statePath(designation));
  }
  await resetEmployee(ADMIN_EMAIL, "E2E Admin", "hr", true);
  await signUp(ADMIN_EMAIL, statePath("admin"));
  await resetBudgets();
  await clearAnswerCache();
}
