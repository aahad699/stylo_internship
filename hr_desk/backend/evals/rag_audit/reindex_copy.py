"""Copy the current chunk index, then rebuild embed text on that copy's sibling rows.

policy_chunks_v1 keeps the previous bodies, embed text, and embeddings.
The live table is what search reads. Restore by copying those columns back by id
and reinserting any id that exists only in policy_chunks_v1.
"""

from __future__ import annotations

import os

os.environ.setdefault("HR_DESK_PUBLIC_ORIGIN", "http://localhost:3000")

from pgvector import Vector

from app.aliases import embed_input
from app.factory import build_embedder, open_store


def _tiny(body: str, title: str) -> bool:
    text = body.strip()
    if not text or len(text.encode("utf-8")) > 80:
        return False
    folded = text.casefold()
    if folded.startswith("page ") and " of " in folded:
        return False
    title_folded = title.strip().casefold()
    plain = folded.strip(" .")
    if plain == title_folded or plain in {"annexures", "code of conduct"}:
        return True
    return len(text.encode("utf-8")) <= 40 and plain in title_folded


def main() -> None:
    store = open_store()
    embedder = build_embedder(store)
    with store.conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.policy_chunks_v1')")
        if cur.fetchone()[0] is None:
            cur.execute("CREATE TABLE policy_chunks_v1 AS SELECT * FROM policy_chunks")
            print("snapshot policy_chunks_v1 created")
        else:
            print("snapshot policy_chunks_v1 already present")
        cur.execute(
            """
            SELECT c.id, c.policy_id, p.title, c.heading, c.heading_path, c.body, c.content_type, c.embed_text
            FROM policy_chunks c
            JOIN policies p ON p.id = c.policy_id
            ORDER BY c.policy_id, c.id
            """
        )
        rows = [
            {
                "id": row[0], "policy_id": row[1], "title": row[2], "heading": row[3],
                "heading_path": row[4] or "", "body": row[5], "content_type": row[6], "embed_text": row[7],
            }
            for row in cur.fetchall()
        ]
    by_policy: dict[str, list[dict]] = {}
    for row in rows:
        by_policy.setdefault(row["policy_id"], []).append(row)
    drop = []
    for group in by_policy.values():
        for index, row in enumerate(group):
            if not _tiny(row["body"], row["title"]):
                continue
            host = next((other for other in group[index + 1:] if not _tiny(other["body"], other["title"])), None)
            if host is None:
                continue
            line = row["body"].strip()
            if line and line not in host["body"]:
                host["body"] = f"{line}\n{host['body']}"
            drop.append(row["id"])
    live = [row for row in rows if row["id"] not in set(drop)]
    for row in live:
        if row["content_type"] == "table" and "tour allowance" in row["heading"].casefold():
            if not row["body"].casefold().startswith("daily tour allowance"):
                row["body"] = f"Daily tour allowance rates. {row['body'].strip()}"
        row["new_embed"] = embed_input(row["title"][:200], row["heading_path"][:600], row["heading"], row["body"])
    pending = [row for row in live if row["new_embed"] != row["embed_text"]]
    vectors = embedder.embed([row["new_embed"] for row in pending]) if pending else []
    with store.conn.cursor() as cur:
        for row_id in drop:
            cur.execute("DELETE FROM policy_chunks WHERE id = %s", (row_id,))
        for row, vector in zip(pending, vectors):
            cur.execute(
                "UPDATE policy_chunks SET body = %s, embed_text = %s, embedding = %s WHERE id = %s",
                (row["body"], row["new_embed"], Vector(vector), row["id"]),
            )
        cur.execute("SELECT count(*) FROM policy_chunks")
        left = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM policy_chunks_v1")
        saved = cur.fetchone()[0]
    print(f"merged_away {len(drop)} reembedded {len(pending)} live {left} snapshot {saved}")
    print("merged_ids " + ",".join(drop))


if __name__ == "__main__":
    main()
