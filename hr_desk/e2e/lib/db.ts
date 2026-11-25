import pg from "pg";

// Same database Start-HRDesk.ps1 uses. The suite only touches e2e.* accounts and
// the request_budget rows the rate limiter keeps.
const DSN = process.env.HR_DESK_E2E_DATABASE_URL ?? "postgresql://hr_desk:hr_desk@127.0.0.1:5433/hr_desk";

export async function withDb<T>(work: (client: pg.Client) => Promise<T>): Promise<T> {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  try {
    return await work(client);
  } finally {
    await client.end();
  }
}

// The limiter allows 120 team questions and 24 sign-ins per IP per 15 minutes,
// which a full matrix run exceeds by design. Clearing it keeps the suite about
// answers, not throttling. Throttling has its own test.
export async function resetBudgets(): Promise<void> {
  await withDb((db) => db.query(
    "DELETE FROM request_budget WHERE scope NOT LIKE 'login:account:%'; DELETE FROM ask_leases;",
  ));
}

// Saved answers are keyed by question and role, so a stale one would hide what the
// current code does. The cache test re-creates entries on purpose.
export async function clearAnswerCache(): Promise<void> {
  await withDb((db) => db.query("DELETE FROM answer_cache"));
}

export async function setDesignation(email: string, designation: string): Promise<void> {
  await withDb((db) => db.query("UPDATE employees SET designation = $2 WHERE email = $1", [email, designation]));
}

export async function resetEmployee(email: string, name: string, designation: string, isAdmin: boolean) {
  await withDb(async (db) => {
    await db.query(
      `INSERT INTO employees (email, name, designation, is_admin, password_hash)
       VALUES ($1, $2, $3, $4, NULL)
       ON CONFLICT (email) DO UPDATE
         SET name = EXCLUDED.name, designation = EXCLUDED.designation,
             is_admin = EXCLUDED.is_admin, password_hash = NULL`,
      [email, name, designation, isAdmin],
    );
    await db.query("DELETE FROM sessions WHERE email = $1", [email]);
    await db.query("DELETE FROM conversations WHERE email = $1", [email]);
  });
}

export async function policyAudiences(): Promise<{ id: string; title: string; audience: string[] }[]> {
  return withDb(async (db) => {
    const { rows } = await db.query("SELECT id, title, audience FROM policies WHERE published ORDER BY title");
    return rows.map((row) => ({ id: row.id, title: row.title, audience: String(row.audience).split(",") }));
  });
}
