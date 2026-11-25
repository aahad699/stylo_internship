import { request, type APIRequestContext } from "@playwright/test";
import { BASE_URL } from "../playwright.config";

export const PASSWORD = "E2E-hr-desk-pass-2026";

// Every designation the backend knows (app/access.py ROLE_LABELS).
export const DESIGNATIONS = [
  "intern", "employee", "manager", "hr", "executive", "trainee", "officer", "senior_executive",
  "assistant_manager", "senior_manager", "assistant_general_manager", "deputy_general_manager",
  "general_manager", "group_chief", "coo_business", "ceo", "cfo", "director", "designer", "supervisor",
  "senior_store_manager", "store_manager", "assistant_store_manager", "branch_accountant", "salesman",
  "regional_sales_manager", "area_sales_manager", "retail_excellence_manager", "people_executive",
  "people_specialist", "regional_people_specialist", "hrbp", "head_hr", "support_staff", "other_staff",
] as const;

export type Designation = (typeof DESIGNATIONS)[number];

export const ADMIN_EMAIL = "e2e.admin@stylo.example";

export function emailFor(designation: string): string {
  return `e2e.${designation.replaceAll("_", "-")}@stylo.example`;
}

export function statePath(designation: string): string {
  return `.auth/${designation}.json`;
}

export async function apiAs(designation: string): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: BASE_URL,
    extraHTTPHeaders: { Origin: BASE_URL },
    storageState: statePath(designation),
  });
}

export async function anonymousApi(): Promise<APIRequestContext> {
  return request.newContext({ baseURL: BASE_URL, extraHTTPHeaders: { Origin: BASE_URL } });
}
