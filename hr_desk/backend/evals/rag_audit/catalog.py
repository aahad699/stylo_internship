"""Read-only corpus catalog. Writes compact JSON. Does not update the database."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

os.environ.setdefault("HR_DESK_PUBLIC_ORIGIN", "http://localhost:3000")

from app.factory import open_store  # noqa: E402

OUT = Path(__file__).resolve().parent / "catalog.json"
_SENTENCE_END = re.compile(r"[.!?…][\"')\]]*$")
_ROWISH = re.compile(r"\bgrade\b|\brs\.?\b|per km|per day", re.I)


def main() -> None:
    store = open_store()
    store.cache_store = lambda row: None
    store.add_trace = lambda trace: None
    store.consume_budget = lambda limits, lease=False: None
    with store.conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'policies'
            ORDER BY ordinal_position
            """
        )
        columns = [row[0] for row in cur.fetchall()]
        cur.execute(
            """
            SELECT p.id, p.title, p.source_name, p.audience, p.effective_from, p.effective_to,
                   p.published, p.superseded_on, p.previous_id,
                   count(c.id) AS passages,
                   coalesce(sum(octet_length(c.body)), 0) AS body_bytes,
                   coalesce(sum(octet_length(c.embed_text)), 0) AS embed_bytes,
                   count(*) FILTER (WHERE c.image_path <> '') AS with_image,
                   count(*) FILTER (WHERE octet_length(c.body) < 80) AS short_bodies,
                   count(*) FILTER (WHERE c.content_type = 'table') AS tables,
                   count(*) FILTER (WHERE c.content_type = 'form') AS forms,
                   count(*) FILTER (WHERE c.page IS NOT NULL) AS paged
            FROM policies p
            LEFT JOIN policy_chunks c ON c.policy_id = p.id
            GROUP BY p.id
            ORDER BY p.title
            """
        )
        policies = []
        for row in cur.fetchall():
            policies.append(
                {
                    "id": row[0],
                    "title": row[1],
                    "source_name": row[2],
                    "audience": row[3],
                    "effective_from": str(row[4]),
                    "effective_to": str(row[5]) if row[5] else "",
                    "published": row[6],
                    "superseded_on": str(row[7]) if row[7] else "",
                    "previous_id": row[8] or "",
                    "passages": row[9],
                    "body_bytes": row[10],
                    "embed_bytes": row[11],
                    "with_image": row[12],
                    "short_bodies": row[13],
                    "tables": row[14],
                    "forms": row[15],
                    "paged": row[16],
                    "type": Path(row[2]).suffix.lower().lstrip(".") if row[2] else "",
                }
            )
        cur.execute(
            """
            SELECT c.id, c.policy_id, p.title, c.heading, c.heading_path, c.content_type,
                   c.page, octet_length(c.body), octet_length(c.embed_text),
                   left(c.body, 90), right(c.body, 90),
                   c.image_path <> '', c.allowed_roles, c.audience, c.effective_from, c.effective_to
            FROM policy_chunks c
            JOIN policies p ON p.id = c.policy_id
            ORDER BY p.title, c.id
            """
        )
        passages = []
        sizes = []
        flags = Counter()
        for row in cur.fetchall():
            body_start = row[9] or ""
            body_end = row[10] or ""
            size = row[7] or 0
            sizes.append(size)
            start_mid = bool(body_start) and body_start[:1].islower()
            end_mid = bool(body_end) and not _SENTENCE_END.search(body_end.strip())
            heading = row[3] or ""
            path = row[4] or ""
            no_heading = heading.strip().casefold() in {"", row[2].strip().casefold()}
            passages.append(
                {
                    "id": row[0],
                    "policy_id": row[1],
                    "title": row[2],
                    "heading": heading,
                    "heading_path": path,
                    "content_type": row[5],
                    "page": row[6],
                    "bytes": size,
                    "embed_bytes": row[8],
                    "start": body_start.replace("\n", " "),
                    "end": body_end.replace("\n", " "),
                    "has_image": row[11],
                    "roles": list(row[12] or []),
                    "audience": row[13],
                    "effective_from": str(row[14]),
                    "effective_to": str(row[15]) if row[15] else "",
                    "start_mid": start_mid,
                    "end_mid": end_mid,
                    "no_heading": no_heading,
                }
            )
            if start_mid:
                flags["start_mid"] += 1
            if end_mid:
                flags["end_mid"] += 1
            if no_heading:
                flags["no_heading"] += 1
            if row[5] == "table" and end_mid:
                flags["table_end_mid"] += 1
        cur.execute(
            """
            SELECT id, heading, octet_length(body)
            FROM policy_chunks
            WHERE content_type = 'table'
               OR heading ~* 'fuel|hotel|allowance|plan|entitlement|grade'
            ORDER BY heading, id
            """
        )
        rate_rows = [
            {"id": row[0], "heading": row[1], "bytes": row[2]}
            for row in cur.fetchall()
        ]
    sizes.sort()
    def pct(q):
        if not sizes:
            return 0
        index = min(len(sizes) - 1, int(q * (len(sizes) - 1)))
        return sizes[index]
    payload = {
        "policy_columns": columns,
        "policies": policies,
        "passage_count": len(passages),
        "size_min": sizes[0] if sizes else 0,
        "size_p50": pct(0.5),
        "size_p90": pct(0.9),
        "size_max": sizes[-1] if sizes else 0,
        "flags": dict(flags),
        "rate_rows": rate_rows,
        "passages": passages,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"policies={len(policies)} passages={len(passages)} wrote {OUT.name}")
    for policy in policies:
        print(
            f"{policy['title'][:48]:48} {policy['type']:5} passages={policy['passages']:4} "
            f"bytes={policy['body_bytes']:7} audience={policy['audience'][:40]}"
        )


if __name__ == "__main__":
    main()
