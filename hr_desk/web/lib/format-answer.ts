export type AnswerBlock =
  | { type: "paragraph"; text: string }
  | { type: "heading"; text: string }
  | { type: "list"; items: string[] }
  | { type: "table"; caption: string; headers: string[]; rows: string[][] };

const GRADE_ROW = /Grade:\s*(.+?)\.\s*Competent Authority:\s*([^.]*)/gi;
const FIELD = /^([^:\n]{2,60}):\s+(\S.*)$/;
const APPROVAL = /^(.+?)\s+[—–-]\s+approved by\s+(.+)$/i;

export function formatAnswer(raw: string, labels: string[] = []): AnswerBlock[] {
  const blocks: AnswerBlock[] = [];
  for (const part of splitTables(raw)) {
    if (part.kind === "table") {
      blocks.push(part.table);
      continue;
    }
    blocks.push(...blocksFromProse(part.text, labels));
  }
  return blocks.length > 0 ? blocks : [{ type: "paragraph", text: raw.trim() }];
}

function splitTables(raw: string): Array<{ kind: "text"; text: string } | { kind: "table"; table: AnswerBlock & { type: "table" } }> {
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const parts: Array<{ kind: "text"; text: string } | { kind: "table"; table: AnswerBlock & { type: "table" } }> = [];
  let prose: string[] = [];
  const flush = () => {
    const text = prose.join("\n").trim();
    if (text) parts.push({ kind: "text", text });
    prose = [];
  };
  for (let index = 0; index < lines.length; index += 1) {
    const header = lines[index].trim();
    const rule = lines[index + 1]?.trim() ?? "";
    if (header.startsWith("|") && /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(rule)) {
      flush();
      const tableLines = [header, rule];
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        tableLines.push(lines[index].trim());
        index += 1;
      }
      index -= 1;
      const table = readMarkdownTable(tableLines);
      if (table) parts.push({ kind: "table", table });
      continue;
    }
    prose.push(lines[index]);
  }
  flush();
  return parts;
}

function readMarkdownTable(lines: string[]): (AnswerBlock & { type: "table" }) | null {
  const cells = (line: string) =>
    line
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => tidy(cell));
  const headers = cells(lines[0]);
  const rows = lines.slice(2).map(cells).filter((row) => row.some(Boolean) && row.length === headers.length);
  if (headers.length < 2 || rows.length === 0) return null;
  return { type: "table", caption: "", headers, rows };
}

function blocksFromProse(raw: string, labels: string[]): AnswerBlock[] {
  const blocks: AnswerBlock[] = [];
  let list: string[] = [];
  const flushList = () => {
    if (list.length === 0) return;
    const caption = takeCaption(blocks);
    const table = tableFromItems(list, caption);
    if (table) blocks.push(table);
    else {
      if (caption) blocks.push({ type: "paragraph", text: `${caption}:` });
      blocks.push({ type: "list", items: list });
    }
    list = [];
  };
  let fields: Array<[string, string]> = [];
  const flushFields = () => {
    if (fields.length === 0) return;
    const caption = takeCaption(blocks);
    if (fields.length >= 2) {
      blocks.push({
        type: "table",
        caption,
        headers: ["Item", "Details"],
        rows: fields.map(([label, value]) => [label, value]),
      });
    } else {
      if (caption) blocks.push({ type: "paragraph", text: `${caption}:` });
      const [label, value] = fields[0];
      blocks.push({ type: "paragraph", text: `${label}: ${value}` });
    }
    fields = [];
  };

  for (const piece of pullRows(raw)) {
    if (piece.kind === "row") {
      flushFields();
      list.push(piece.text);
      continue;
    }
    flushList();
    const heading = piece.text.match(/^\*\*([^*\n]+)\*\*$/);
    if (heading) {
      flushFields();
      blocks.push({ type: "heading", text: heading[1] });
      continue;
    }
    const lines = /^\d+(?:\.\d+)+\.?\s/.test(piece.text) ? [piece.text] : sentences(piece.text);
    for (const sentence of lines) {
      if (isHeadingEcho(sentence, labels)) continue;
      const field = sentence.match(FIELD);
      if (field) {
        fields.push([tidy(field[1]), tidy(field[2])]);
        continue;
      }
      flushFields();
      blocks.push({ type: "paragraph", text: sentence });
    }
  }
  flushList();
  flushFields();
  return blocks;
}

function tableFromItems(items: string[], caption: string): (AnswerBlock & { type: "table" }) | null {
  const fields = items.map((item) => item.match(FIELD));
  if (fields.every(Boolean) && items.length >= 2) {
    return {
      type: "table",
      caption,
      headers: ["Item", "Details"],
      rows: fields.map((match) => [tidy(match?.[1] ?? ""), tidy(match?.[2] ?? "")]),
    };
  }
  const approvals = items.map((item) => item.match(APPROVAL));
  if (approvals.every(Boolean) && items.length >= 1) {
    return {
      type: "table",
      caption,
      headers: ["Grade", "Approved by"],
      rows: approvals.map((match) => [tidy(match?.[1] ?? ""), tidy(match?.[2] ?? "")]),
    };
  }
  return null;
}

function takeCaption(blocks: AnswerBlock[]): string {
  const last = blocks[blocks.length - 1];
  if (last?.type !== "paragraph" || !last.text.endsWith(":") || last.text.length > 80) return "";
  blocks.pop();
  return last.text.slice(0, -1);
}

function pullRows(raw: string): Array<{ kind: "text" | "row"; text: string }> {
  const normalized = raw
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+-\s+(?=Grade:)/gi, "\n")
    .trim();
  const pieces: Array<{ kind: "text" | "row"; text: string }> = [];
  for (const rawLine of normalized.split("\n")) {
    const trimmed = rawLine.trim();
    if (!trimmed) continue;
    const bullet = /^[-•]\s+/.test(trimmed);
    const line = trimmed.replace(/^[-•]\s+/, "").trim();
    if (/grade:/i.test(line) && /competent authority:/i.test(line)) {
      const expression = new RegExp(GRADE_ROW.source, "gi");
      let last = 0;
      let matched = false;
      for (const match of line.matchAll(expression)) {
        matched = true;
        const before = line
          .slice(last, match.index)
          .replace(/^[-•]\s*/, "")
          .trim();
        if (before) pieces.push({ kind: "text", text: before });
        const who = tidy(match[1]);
        const by = tidy(match[2]);
        if (who && by) pieces.push({ kind: "row", text: `${who} — approved by ${by}` });
        last = (match.index ?? 0) + match[0].length;
      }
      const after = line
        .slice(last)
        .replace(/^[\s.\-–—]+/, "")
        .trim();
      if (after) pieces.push({ kind: "text", text: after });
      if (!matched) pieces.push({ kind: bullet ? "row" : "text", text: line });
      continue;
    }
    pieces.push({ kind: bullet ? "row" : "text", text: line });
  }
  return pieces;
}

function sentences(text: string): string[] {
  return text
    .split(/(?<!\d\.)(?<=[.!?])\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function isHeadingEcho(sentence: string, labels: string[]): boolean {
  const value = plain(sentence);
  if (!value || value.split(" ").length > 8) return false;
  return labels.some((label) => {
    const folded = plain(label);
    return folded === value || folded.endsWith(` ${value}`);
  });
}

function plain(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function tidy(text: string): string {
  return text.replace(/\s+/g, " ").replace(/[.\s]+$/, "").trim();
}
