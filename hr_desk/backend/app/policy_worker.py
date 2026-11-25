"""Process persisted policy jobs outside HTTP requests."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import date

from .ingest import remove_policy_files, replacement_policy, write_policy_file
from .llm import provider_deadline
from .models import ChunkDraft, PolicyDraft

logger = logging.getLogger(__name__)


async def run_policy_worker(pipeline) -> None:
    store = pipeline.store
    while True:
        try:
            job = await asyncio.to_thread(store.claim_policy_job)
            if job is not None:
                task = asyncio.create_task(asyncio.to_thread(process_policy_job, pipeline, job))
                while not task.done():
                    await asyncio.wait([task], timeout=20)
                    if not task.done():
                        await asyncio.to_thread(store.renew_policy_job, job["id"], job["claim"])
                await task
                continue
        except Exception as error:
            logger.error("error_class=%s request_id=- status=500", type(error).__name__)
        await asyncio.sleep(2)


def process_policy_job(pipeline, job: dict) -> None:
    store = pipeline.store
    claim = (job["id"], job["claim"])
    try:
        employee = store.employee(job["actor"])
        if employee is None or not employee.is_admin:
            raise ValueError("Ask an HR admin to review and retry this job.")
        if job["kind"] == "prepare":
            _prepare(store, job, claim)
        else:
            _publish(pipeline, job, claim)
    except ValueError as exc:
        store.fail_policy_job(*claim, str(exc))
    except Exception as error:
        logger.error("error_class=%s request_id=%s status=500", type(error).__name__, job["id"])
        store.fail_policy_job(*claim, "Processing did not finish. Your upload or draft is saved for retry.")


def _prepare(store, job, claim) -> None:
    fields = job["payload"]
    start = date.fromisoformat(fields["starts_on"])
    end = date.fromisoformat(fields["ends_on"]) if fields["ends_on"] else None
    try:
        draft, source = replacement_policy(fields["title"], fields["audience"], start, end,
                                           fields["text"], fields["filename"], job["upload"])
    except ValueError as exc:
        raise ValueError("We couldn't extract searchable content. Check the file or add a text layer.") from exc
    if not draft.chunks or len(draft.chunks) > 500:
        raise ValueError("Use a policy with up to 500 searchable passages.")
    prepared = asdict(draft)
    prepared.update(effective_from=start.isoformat(), effective_to=end.isoformat() if end else None,
                    replaces=fields["replaces"])
    try:
        if source:
            write_policy_file(source, job["upload"])
        store.save_draft(job["id"], prepared, job_claim=claim)
    except Exception:
        remove_policy_files(draft.id)
        raise


def _publish(pipeline, job, claim) -> None:
    store = pipeline.store
    key = job["payload"]["draft_key"]
    record = store.get_draft(key)
    if record is None:
        raise ValueError("That draft is no longer available.")
    fields = {field: record[field] for field in ("id", "title", "audience", "body", "source_name", "warnings")}
    draft = PolicyDraft(**fields,
                        effective_from=date.fromisoformat(record["effective_from"]),
                        effective_to=date.fromisoformat(record["effective_to"]) if record["effective_to"] else None,
                        chunks=[ChunkDraft(**item) for item in record["chunks"]])
    if record["replaces"] and not store.has_policy(record["replaces"]):
        raise ValueError("The policy being replaced has changed. Prepare a new draft.")
    with provider_deadline(180):
        vectors = pipeline.embedder.embed([chunk.embed_text for chunk in draft.chunks])
    store.insert_policy(draft, draft.chunks, vectors, replaces=record["replaces"], draft_key=key,
                        actor=job["actor"], as_of=pipeline.today(), job_claim=claim)
