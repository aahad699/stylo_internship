import type { APIRequestContext } from "@playwright/test";
import { resetBudgets } from "./db";

export type Outcome = "answered" | "refused" | "dont_know" | "clarification" | "blocked" | string;

export interface Turn {
  question: string;
  status: number;
  outcome: Outcome;
  answer: string;
}

async function post(api: APIRequestContext, url: string, data?: object) {
  for (let attempt = 0; ; attempt++) {
    const response = await api.post(url, data ? { data, timeout: 90_000 } : { timeout: 90_000 });
    if (response.status() !== 429 || attempt >= 4) return response;
    await resetBudgets();
    await new Promise((done) => setTimeout(done, 2_000 * (attempt + 1)));
  }
}

export async function newConversation(api: APIRequestContext): Promise<string> {
  const response = await post(api, "/api/conversations");
  if (!response.ok()) throw new Error(`new conversation -> ${response.status()} ${await response.text()}`);
  const body = await response.json();
  return body.id ?? body.conversation?.id ?? body.key;
}

export async function ask(api: APIRequestContext, conversationId: string, question: string): Promise<Turn> {
  const response = await post(api, "/api/ask", { question, conversation_id: conversationId });
  const body = await response.json().catch(() => ({}));
  return {
    question,
    status: response.status(),
    outcome: body.outcome ?? `http_${response.status()}`,
    answer: body.answer ?? body.detail ?? "",
  };
}

export async function askThread(api: APIRequestContext, questions: string[]): Promise<Turn[]> {
  const id = await newConversation(api);
  const turns: Turn[] = [];
  for (const question of questions) turns.push(await ask(api, id, question));
  return turns;
}
