import { defineConfig, devices } from "@playwright/test";

// The API rejects writes whose Origin differs from HR_DESK_PUBLIC_ORIGIN, so the
// browser must use the exact origin Start-HRDesk.ps1 configures.
export const BASE_URL = process.env.HR_DESK_E2E_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "tests",
  timeout: 120_000,
  expect: { timeout: 45_000 },
  fullyParallel: true,
  // The API allows four concurrent questions team-wide.
  workers: 3,
  retries: 0,
  reporter: [
    ["list"],
    ["json", { outputFile: "report/results.json" }],
    ["html", { open: "never", outputFolder: "playwright-report" }],
  ],
  globalSetup: "./lib/global-setup.ts",
  use: {
    baseURL: BASE_URL,
    extraHTTPHeaders: { Origin: BASE_URL },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "ui", testMatch: /.*\.ui\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    { name: "access", testMatch: /.*\.access\.spec\.ts/ },
    { name: "matrix", testMatch: /.*\.matrix\.spec\.ts/ },
  ],
});
