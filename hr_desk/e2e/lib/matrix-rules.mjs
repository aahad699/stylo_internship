export const norm = (text) => text.toLowerCase().replace(/(\d),(\d)/g, "$1$2").replace(/\s+/g, " ");

export function containsForbidden(answer, phrase) {
  const normalized = norm(phrase);
  const escaped = normalized.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const end = /[a-z0-9]$/.test(normalized) ? "(?![a-z0-9])" : "";
  return new RegExp(escaped + end).test(norm(answer));
}

export function missingExpectedFact(outcome, answer, choices) {
  return outcome === "answered" && Boolean(choices?.length)
    && !choices.some((choice) => norm(answer).includes(norm(choice)));
}
