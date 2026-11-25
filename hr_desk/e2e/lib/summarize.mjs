import { readdirSync, readFileSync, writeFileSync } from "node:fs";

const verdicts = readdirSync("report/matrix").map((f) => JSON.parse(readFileSync(`report/matrix/${f}`, "utf8")));
const groups = {};
for (const v of verdicts) {
  const kind = v.pass ? "pass"
    : v.leaked.length ? "LEAK"
    : !v.outcomeOk && v.expect === "refused" && v.turns.at(-1).outcome === "answered" ? "should-refuse"
    : !v.outcomeOk && v.expect === "answered" && ["refused"].includes(v.turns.at(-1).outcome) ? "over-blocked"
    : !v.outcomeOk && v.expect === "answered" ? "not-answered"
    : !v.factOk ? "wrong-fact"
    : "wrong-outcome";
  (groups[`${v.category}/${kind}`] ??= []).push(v);
}

const lines = ["# Matrix verdicts", "", `${verdicts.length} cases, ${verdicts.filter((v) => v.pass).length} pass`, ""];
for (const key of Object.keys(groups).sort()) {
  lines.push(`## ${key} (${groups[key].length})`, "");
  if (key.endsWith("/pass")) { lines.push(groups[key].map((v) => v.id).join(", "), ""); continue; }
  for (const v of groups[key]) {
    const last = v.turns.at(-1);
    lines.push(`- **${v.id}** [${v.designation}] expect ${v.expect}, got ${last.outcome}`);
    if (v.history?.length) lines.push(`  - history: ${v.history.join(" / ")}`);
    lines.push(`  - Q: ${v.question}`, `  - A: ${last.answer.replace(/\s+/g, " ").slice(0, 300)}`);
    if (v.must_contain_any?.length) lines.push(`  - wanted one of: ${JSON.stringify(v.must_contain_any)}`);
    if (v.leaked.length) lines.push(`  - leaked: ${JSON.stringify(v.leaked)}`);
    if (v.source) lines.push(`  - source: ${v.source}`);
  }
  lines.push("");
}
writeFileSync("report/matrix-summary.md", lines.join("\n"));
for (const key of Object.keys(groups).sort()) console.log(key.padEnd(34), groups[key].length);
