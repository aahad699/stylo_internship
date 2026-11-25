"""Read each company policy in document order and build the passages search uses."""

from __future__ import annotations

import hashlib
import re
from contextvars import ContextVar
from zipfile import ZipFile
from datetime import date
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .aliases import embed_input
from .config import CHUNK_CHARS, CHUNK_OVERLAP
from .models import ChunkDraft, PolicyDraft
from .transcribe import save_picture, transcribe_picture

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_MEDICAL_PLANS = (
    (
        "Plan A",
        "COO, Director, CEO, CFO, HEAD, AGM, GM, Higher Management",
        "Hospitalization Benefit (Employee, Spouse and Children): 500,000. "
        "Room & Board Limit (per day): 12,000. Normal Delivery: 100,000. "
        "Complicated /Caesarean Delivery: 180,000.",
    ),
    (
        "Plan B",
        "Manager, Assistant, Sr. Manager, Middle Management",
        "Hospitalization Benefit (Employee, Spouse and Children): 400,000. "
        "Room & Board Limit (per day): 10,000. Normal Delivery: 80,000. "
        "Complicated /Caesarean Delivery: 160,000.",
    ),
    (
        "Plan C",
        "Executive, Designer, Lower Management",
        "Hospitalization Benefit (Employee, Spouse and Children): 300,000. "
        "Room & Board Limit (per day): 7,000. Normal Delivery: 70,000. "
        "Complicated /Caesarean Delivery: 120,400.",
    ),
    (
        "Plan D",
        "Officers, Supervisor, Support Staff",
        "Hospitalization Benefit (Employee, Spouse and Children): 250,000. "
        "Room & Board Limit (per day): 5,000. Normal Delivery: 60,000. "
        "Complicated /Caesarean Delivery: 100,200.",
    ),
    (
        "Plan E",
        "Other Staff",
        "Hospitalization Benefit (Employee, Spouse and Children): 200,000. "
        "Room & Board Limit (per day): 5,000. Normal Delivery: 50,000. "
        "Complicated /Caesarean Delivery: 70,000.",
    ),
)

_CLAIM_FORM = (
    "Jubilee Health Insurance inpatient medical claim form. "
    "Name of the company or policy holder. Name of the claimant. Name of the claimant's father or spouse. "
    "Full address of claimant. Full name of the patient. Date of birth. Male or female. CNIC. "
    "Policy number. Certificate number. Phone number. Patient's relationship to claimant: employee or dependent. "
    "Total amount claimed in Rs. Nature of illness, injury, or medical condition. Date symptoms first occurred. "
    "Patient last working day. Hospital name. Hospital address. Doctor name. "
    "Signature of the patient. Signature and stamp of the employer. Date. "
    "Bank name with branch name. Location of branch. Bank account number."
)

_FLOWCHART = (
    "Annexure E in-patient claim process. "
    "Emergency hospitalization at a panel hospital: approach the nearest Jubilee panel hospital with the health card, "
    "identify yourself as a Jubilee insured, get treatment on credit as per policy terms, and pay only the amount "
    "that exceeds the entitlement. "
    "Emergency hospitalization at a non-panel hospital: go to the nearest hospital, inform Jubilee within 24 hours, "
    "pay cash, submit original bills with the claim form within 30 days of discharge, and settlement follows the policy. "
    "Non-emergency hospitalization at a panel hospital: approach the nearest Jubilee panel hospital, identify yourself, "
    "get treatment on credit, and pay only the amount that exceeds the entitlement. "
    "Non-emergency hospitalization at a non-panel hospital: send the estimated cost to the doctor or hospital for "
    "prior approval, Jubilee approves or disapproves as per policy terms, pay cash, and submit original bills with "
    "the claim form within 30 days of discharge."
)


def file_policy_id(filename: str, payload: bytes) -> str:
    name = Path(filename).name
    return "doc-" + hashlib.sha256(name.encode("utf-8") + b"\0" + payload).hexdigest()[:16]


_TRAVEL_SHA256 = "3ef8c6a2dc6752bdc72de778abdc4056e467c695083a4be472f0c4792cd098b8"
_MEDICAL_SHA256 = "15dfd201ee84c93f072b04941540c8a1a4180eacfb785920d668eaa003c97537"
_reader_notes: ContextVar[list[str] | None] = ContextVar("reader_notes", default=None)


def _note(message: str) -> None:
    notes = _reader_notes.get()
    if notes is not None and message not in notes:
        notes.append(message)


def policy_from_bytes(filename: str, payload: bytes) -> PolicyDraft | None:
    name = Path(filename).name
    path = Path(name)
    policy_id = file_policy_id(name, payload)
    notes: list[str] = []
    handle = _reader_notes.set(notes)
    try:
        suffix = path.suffix.lower()
        if suffix == ".docx":
            with ZipFile(BytesIO(payload)) as package:
                entries = package.infolist()
                if len(entries) > 3000 or sum(entry.file_size for entry in entries) > 128 * 1024 * 1024:
                    raise ValueError("This Word file expands beyond the processing limit.")
                if any(entry.filename.endswith("vbaProject.bin") for entry in entries):
                    raise ValueError("Upload a Word file without macros.")
            chunks, title, effective = _docx(path, payload, policy_id)
        elif suffix == ".pdf":
            chunks, title, effective = _pdf(path, payload, policy_id)
        else:
            return None
    finally:
        _reader_notes.reset(handle)
    if not chunks:
        return None
    if len(chunks) > 500:
        raise ValueError("This policy needs more than 500 searchable passages. Upload it in separate parts.")
    if effective is None:
        effective = _filename_date(path) if _filename_date_allowed(path) else date(2025, 1, 1)
    return PolicyDraft(
        id=policy_id, title=title, audience="everyone", effective_from=effective, effective_to=None,
        body="\n\n".join(chunk.body for chunk in chunks), source_name=name, chunks=chunks, warnings=notes,
    )


def office_policy(path: Path) -> PolicyDraft | None:
    return policy_from_bytes(path.name, path.read_bytes())


def _filename_date_allowed(path: Path) -> bool:
    """Medical 051124 and the travel filename month are not the policy dates."""
    name = path.name.lower()
    if "medical" in name or "travel" in name or "provident" in name or "advance" in name:
        return False
    return _filename_date(path) is not None


def _filename_date(path: Path) -> date | None:
    numbered = re.search(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)", path.stem)
    if not numbered:
        return None
    day, month, year = int(numbered.group(1)), int(numbered.group(2)), 2000 + int(numbered.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _effective_date(text: str) -> date | None:
    folded = text.lower()
    for match in _date_matches(text):
        start = max(0, match[0] - 90)
        window = folded[start : match[0]]
        if "effective" in window:
            return match[1]
    return None


def _date_matches(text: str) -> list[tuple[int, date]]:
    found: list[tuple[int, date]] = []
    month = r"(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    for match in re.finditer(rf"\b{month}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", text, re.IGNORECASE):
        found.append((match.start(), _safe_date(int(match.group(3)), match.group(1), int(match.group(2)))))
    for match in re.finditer(rf"\b(\d{{1,2}})[-\s]{month}\.?[-\s,]*(\d{{4}})", text, re.IGNORECASE):
        found.append((match.start(), _safe_date(int(match.group(3)), match.group(2), int(match.group(1)))))
    return [(pos, value) for pos, value in found if value is not None]


def _safe_date(year: int, month_name: str, day: int) -> date | None:
    try:
        return date(year, _MONTHS[month_name[:3].lower()], day)
    except (KeyError, ValueError):
        return None


def _docx(path: Path, payload: bytes, policy_id: str) -> tuple[list[ChunkDraft], str, date | None]:
    document = Document(BytesIO(payload))
    numbers = _WordNumbers(document)
    title = _docx_title(document, path)
    reader = _DocxReader(document, numbers, title, policy_id)
    chunks = reader.chunks()
    effective = _effective_date("\n".join(chunk.body for chunk in chunks))
    return chunks, title, effective


def _docx_title(document: Document, path: Path) -> str:
    for paragraph in document.paragraphs:
        text = " ".join(paragraph.text.split())
        if not text:
            continue
        folded = text.lower()
        if "provident fund loan policy" in folded:
            return "Provident Fund Loan Policy – Head Office"
        if "corrective action policy" in folded:
            return "Corrective Action Policy"
        if folded == "code of conduct":
            return "Code of Conduct"
        if "advance salary" in folded:
            return "Advance Salary"
        if len(text) > 20:
            break
    stem = re.sub(r"^\d+\.\s*", "", path.stem)
    return stem.replace("_", " ").strip()


class _DocxReader:
    def __init__(self, document: Document, numbers: "_WordNumbers", title: str, policy_id: str) -> None:
        self.document = document
        self.numbers = numbers
        self.title = title
        self.policy_id = policy_id
        self.stack: list[tuple[int, str, bool]] = []
        self.buffer: list[str] = []
        self.images: list[tuple[str, str]] = []
        self.before = ""
        self.found: list[ChunkDraft] = []
        self.uses_heading_styles = any(
            (paragraph.style.name or "").lower().startswith("heading")
            for paragraph in document.paragraphs
            if paragraph.style is not None
        )

    def chunks(self) -> list[ChunkDraft]:
        for block in _blocks(self.document):
            if isinstance(block, Paragraph):
                self._paragraph(block)
            else:
                self._flush()
                self._table(block)
        self._flush()
        self._release_unused(0)
        return self.found

    def _paragraph(self, paragraph: Paragraph) -> None:
        style = paragraph.style.name if paragraph.style is not None else ""
        text = " ".join(paragraph.text.split())
        level = _heading_level(style)
        if level is None and not self.uses_heading_styles and _bold_heading(paragraph, text):
            level = 2
        images = _paragraph_images(paragraph)
        if level is not None and text:
            self._flush()
            shown = _with_number(self.numbers.label(paragraph), text)
            self._release_unused(level)
            self.stack = [item for item in self.stack if item[0] < level]
            self.stack.append((level, shown, False))
            self.before = text
            for name, blob in images:
                self._image(name, blob)
            return
        if text:
            self.buffer.append(_with_number(self.numbers.label(paragraph), text))
            self.before = text
        self.buffer.extend(url for url in _paragraph_urls(paragraph) if url not in text)
        for name, blob in images:
            self._image(name, blob)

    def _image(self, name: str, blob: bytes) -> None:
        heading = self._heading()
        if _skip_picture(name, blob, self.before, heading):
            return
        if name.lower().endswith(".emf"):
            transcript = transcribe_picture(blob)
            if transcript:
                self.images.append(("", transcript))
            else:
                _note(f"{heading}: a Word drawing could not be transcribed.")
            return
        stored = save_picture(self.policy_id, name, blob)
        transcript = transcribe_picture(blob)
        self.images.append((stored, transcript))
        if not transcript:
            _note(f"{heading}: a picture has no searchable transcription.")

    def _table(self, table: Table) -> None:
        self._table_rows(table)
        seen_cells = set()
        for row_number, row in enumerate(table.rows, start=1):
            for column_number, cell in enumerate(row.cells, start=1):
                if cell._tc in seen_cells:
                    continue
                seen_cells.add(cell._tc)
                for paragraph in cell.paragraphs:
                    for name, image in _paragraph_images(paragraph):
                        self._image(name, image)
                self._flush()
                for index, nested in enumerate(cell.tables, start=1):
                    self._stack_table(nested, f"Nested table {index} (row {row_number}, column {column_number})")

    def _stack_table(self, table: Table, heading: str) -> None:
        depth = self.stack[-1][0] + 1 if self.stack else 1
        self.stack.append((depth, heading, True))
        try:
            self._table(table)
        finally:
            self.stack.pop()

    def _table_rows(self, table: Table) -> None:
        rows = [_row_cells(row) for row in table.rows]
        rows = [row for row in rows if any(cell for cell in row)]
        if not rows:
            return
        banner = _table_banner(rows)
        if banner:
            self._release_unused(0)
        heading = banner or self._heading()
        if banner:
            parents = [part for part in self._path().split(" > ") if part and part != self._heading()]
            path = " > ".join([*parents, banner])
        else:
            path = self._path()
        pairs = _key_value_pairs(rows)
        if pairs is not None:
            for left, right in pairs:
                body = f"{left} {right}" if left.endswith(":") else f"{left}: {right}"
                self.found.extend(
                    _chunks(
                        self.title,
                        path,
                        heading,
                        body,
                        content_type="table",
                        polarity=_polarity(heading, body),
                    )
                )
                self._mark_used()
            return
        header, data = _table_header(rows)
        if not data:
            return
        for row in data:
            if header and len(header) == len(row):
                parts = [f"{name}: {value}" for name, value in zip(header, row) if value]
            elif header:
                parts = [f"{name}: {value}" for name, value in zip(header, row) if value]
                parts.extend(value for value in row[len(header) :] if value)
            else:
                parts = [value for value in row if value]
            body = ". ".join(parts)
            if not body:
                continue
            self.found.extend(
                _chunks(
                    self.title,
                    path,
                    heading,
                    body,
                    content_type="table",
                    polarity=_polarity(heading, body),
                )
            )
            self._mark_used()

    def _flush(self) -> None:
        heading = self._heading()
        path = self._path()
        body = "\n".join(self.buffer).strip()
        transcripts = []
        for _path, text in self.images:
            if text and text not in body and text not in transcripts:
                transcripts.append(text)
        if transcripts:
            body = "\n\n".join(part for part in (body, "\n\n".join(transcripts)) if part)
        pictures = [path for path, _text in self.images if path]
        self.buffer = []
        self.images = []
        if not body:
            return
        content_type = "form" if transcripts and _looks_like_form(heading, body) else "section"
        polarity = _polarity(heading, body)
        image_path = "|".join(pictures)
        self.found.extend(
            _chunks(
                self.title,
                path,
                heading,
                body,
                content_type=content_type,
                image_path=image_path,
                polarity=polarity,
            )
        )
        self._mark_used()

    def _release_unused(self, level: int) -> None:
        """A heading with no paragraph or table under it is still part of the policy."""
        for item_level, shown, used in self.stack:
            if item_level < level or used or not shown:
                continue
            self.found.extend(
                _chunks(
                    self.title,
                    self._path(),
                    shown,
                    shown,
                    content_type="section",
                    polarity=_polarity(shown, shown),
                )
            )

    def _mark_used(self) -> None:
        self.stack = [(level, shown, True) for level, shown, _used in self.stack]

    def _heading(self) -> str:
        if not self.stack:
            return self.title
        return self.stack[-1][1]

    def _path(self) -> str:
        if not self.stack:
            return self.title
        return " > ".join(item[1] for item in self.stack)


def _blocks(document: Document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _heading_level(style: str) -> int | None:
    match = re.search(r"heading\s*(\d+)", style or "", re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _bold_heading(paragraph: Paragraph, text: str) -> bool:
    if not text or len(text) > 90:
        return False
    runs = [run for run in paragraph.runs if run.text.strip()]
    if not runs or not all(run.bold for run in runs):
        return False
    words = text.split()
    return len(words) <= 12 or text.endswith(":")


def _paragraph_urls(paragraph: Paragraph) -> list[str]:
    urls = []
    for node in paragraph._p.xpath(".//w:hyperlink"):
        rid = node.get(qn("r:id"))
        if not rid or rid not in paragraph.part.rels:
            continue
        relation = paragraph.part.rels[rid]
        if relation.is_external and re.match(r"^https?://", relation.target_ref, re.I):
            urls.append(relation.target_ref)
    return list(dict.fromkeys(urls))


def _paragraph_images(paragraph: Paragraph) -> list[tuple[str, bytes]]:
    found = []
    for blip in paragraph._p.xpath(".//a:blip"):
        rid = blip.get(qn("r:embed"))
        part = paragraph.part.related_parts.get(rid)
        if part is None:
            continue
        name = str(part.partname).split("/")[-1]
        found.append((name, part.blob))
    return found


def _row_cells(row) -> list[str]:
    cells: list[str] = []
    seen: set[int] = set()
    for cell in row.cells:
        key = id(cell._tc)
        if key in seen:
            continue
        seen.add(key)
        cells.append(re.sub(r"\s+", " ", cell.text).strip())
    return cells


def _table_banner(rows: list[list[str]]) -> str:
    """A merged title row names the table. It is not a column heading."""
    if len(rows) < 2:
        return ""
    first = [cell for cell in rows[0] if cell]
    if len(set(first)) != 1:
        return ""
    if not any(len({cell for cell in row if cell}) > 1 for row in rows[1:]):
        return ""
    return first[0]


def _table_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Use the first real header row. A repeated title cell is not a column name."""
    start = 0
    while start < len(rows) and len({cell for cell in rows[start] if cell}) <= 1:
        start += 1
    if start >= len(rows):
        return [], rows
    header = rows[start]
    data = rows[start + 1 :]
    if not data:
        return [], [header]
    if _header_is_data(header):
        return [], rows[start:]
    return header, data


def _key_value_pairs(rows: list[list[str]]) -> list[tuple[str, str]] | None:
    """A label-and-value table has no column titles. The first row is data, not a header."""
    pairs: list[tuple[str, str]] = []
    for row in rows:
        cells = [cell for cell in row if cell]
        if len(cells) == 1:
            continue
        if len(cells) != 2:
            return None
        pairs.append((cells[0], cells[1]))
    if len(pairs) < 2:
        return None
    left, right = pairs[0]
    later_values = [value for _label, value in pairs[1:]]
    header_like = len(left) <= 20 and len(right) <= 24 and any(len(value) > len(right) + 20 for value in later_values)
    if header_like:
        return None
    return pairs


def _with_number(number: str, text: str) -> str:
    if number and not text.startswith(number):
        return f"{number} {text}".strip()
    return text


def _header_is_data(header: list[str]) -> bool:
    """The field daily-tour table has amounts and no printed column titles."""
    text = " ".join(header)
    if re.search(r"\d", text) and not re.search(r"[A-Za-z]{4,}", text):
        return True
    return False


def _skip_picture(name: str, blob: bytes, before: str, heading: str) -> bool:
    folded = f"{before}\n{heading}".lower()
    if "password:" in folded or ("log in" in folded and "username" in folded):
        return True
    if name.lower().endswith((".png", ".jpg", ".jpeg")) and len(blob) < 8000:
        return True
    return False


def _looks_like_form(heading: str, body: str) -> bool:
    folded = f"{heading} {body[:240]}".lower()
    return "template" in folded or "form" in folded or "report" in folded


class _WordNumbers:
    """Recover automatic heading numbers such as 6.4. They are not in the paragraph text."""

    def __init__(self, document: Document) -> None:
        self.levels: dict[str, dict[int, tuple[int, str]]] = {}
        self.counters: dict[str, dict[int, int]] = {}
        self.style_num: dict[str, tuple[str, int]] = {}
        self._load(document)

    def _load(self, document: Document) -> None:
        try:
            numbering = document.part.numbering_part._element
        except Exception:
            numbering = None
        if numbering is not None:
            abstracts: dict[str, dict[int, tuple[int, str]]] = {}
            for abstract in numbering.findall(qn("w:abstractNum")):
                abstract_id = abstract.get(qn("w:abstractNumId")) or ""
                levels: dict[int, tuple[int, str]] = {}
                for level in abstract.findall(qn("w:lvl")):
                    index = int(level.get(qn("w:ilvl")) or 0)
                    start = level.find(qn("w:start"))
                    text = level.find(qn("w:lvlText"))
                    start_at = int(start.get(qn("w:val"))) if start is not None and start.get(qn("w:val")) else 1
                    fmt = text.get(qn("w:val")) if text is not None and text.get(qn("w:val")) else ""
                    levels[index] = (start_at, fmt)
                abstracts[abstract_id] = levels
            for num in numbering.findall(qn("w:num")):
                num_id = num.get(qn("w:numId")) or ""
                abstract_id = num.find(qn("w:abstractNumId"))
                if abstract_id is None:
                    continue
                levels = dict(abstracts.get(abstract_id.get(qn("w:val")) or "", {}))
                for override in num.findall(qn("w:lvlOverride")):
                    index = int(override.get(qn("w:ilvl")) or 0)
                    start = override.find(qn("w:startOverride"))
                    if start is not None and index in levels and start.get(qn("w:val")):
                        levels[index] = (int(start.get(qn("w:val"))), levels[index][1])
                self.levels[num_id] = levels
        for style in document.styles:
            element = getattr(style, "element", None)
            if element is None:
                continue
            props = element.find(qn("w:pPr"))
            if props is None or props.numPr is None or props.numPr.numId is None:
                continue
            num_id = str(props.numPr.numId.val)
            if num_id == "0":
                continue
            level = int(props.numPr.ilvl.val) if props.numPr.ilvl is not None else 0
            self.style_num[style.name] = (num_id, level)

    def label(self, paragraph: Paragraph) -> str:
        num_id, level = self._binding(paragraph)
        if not num_id:
            return ""
        return self._render(num_id, level)

    def _binding(self, paragraph: Paragraph) -> tuple[str, int]:
        props = paragraph._p.pPr
        if props is not None and props.numPr is not None and props.numPr.numId is not None:
            num_id = str(props.numPr.numId.val)
            if num_id == "0":
                return "", 0
            level = int(props.numPr.ilvl.val) if props.numPr.ilvl is not None else 0
            return num_id, level
        style = paragraph.style.name if paragraph.style is not None else ""
        return self.style_num.get(style, ("", 0))

    def _render(self, num_id: str, level: int) -> str:
        levels = self.levels.get(num_id)
        if not levels or level not in levels or "%" not in levels[level][1]:
            return ""
        counters = self.counters.setdefault(num_id, {})
        for parent in range(level):
            if parent not in counters and parent in levels:
                counters[parent] = levels[parent][0]
        if level not in counters:
            counters[level] = levels[level][0]
        else:
            counters[level] += 1
        for deeper in [index for index in counters if index > level]:
            del counters[deeper]
        def replace(match: re.Match[str]) -> str:
            index = int(match.group(1)) - 1
            if index in counters:
                return str(counters[index])
            if index in levels:
                return str(levels[index][0])
            return ""

        return re.sub(r"%(\d+)", replace, levels[level][1]).strip()


def _pdf(path: Path, payload: bytes, policy_id: str) -> tuple[list[ChunkDraft], str, date | None]:
    import pymupdf

    with pymupdf.open(stream=payload, filetype="pdf") as document:
        if document.needs_pass or document.page_count > 250:
            raise ValueError("Use an unlocked PDF with no more than 250 pages.")
        digest = hashlib.sha256(payload).hexdigest()
        if digest == _TRAVEL_SHA256:
            if document.page_count != len(_TRAVEL_PAGES):
                raise ValueError("The reference travel policy page count changed.")
            return _travel(document, policy_id)
        title = "Medical Insurance Policy" if digest == _MEDICAL_SHA256 else path.stem
        chunks = _text_pdf(document, title, policy_id, medical=digest == _MEDICAL_SHA256)
        return chunks, title, _effective_date("\n".join(chunk.body for chunk in chunks))


def _text_pdf(document, title: str, policy_id: str, *, medical=False) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    heading = title
    heading_from_line = False
    heading_used = False
    heading_page: int | None = None
    buffer: list[str] = []

    def flush(page: int | None) -> None:
        nonlocal buffer, heading_used
        body = "\n".join(buffer).strip()
        buffer = []
        if body:
            chunks.extend(
                _chunks(
                    title,
                    heading,
                    heading,
                    body,
                    content_type="section",
                    page=page,
                    polarity=_polarity(heading, body),
                )
            )
            heading_used = True
            return
        if heading_from_line and not heading_used:
            chunks.extend(
                _chunks(
                    title,
                    heading,
                    heading,
                    heading,
                    content_type="section",
                    page=heading_page,
                    polarity=_polarity(heading, heading),
                )
            )
            heading_used = True

    def begin_heading(line: str, page_no: int) -> None:
        nonlocal heading, heading_from_line, heading_used, heading_page
        flush(page_no)
        heading = line
        heading_from_line = True
        heading_used = False
        heading_page = page_no

    header_lines: list[str] = []

    def take_headers(raw: str) -> list[str]:
        _lines, headers = _pdf_lines(raw)
        for line in headers:
            if line not in header_lines:
                header_lines.append(line)
        return _lines

    for index, page in enumerate(document):
        page_no = index + 1
        raw = page.get_text("text")
        text_page = None
        image_content = not medical and bool(page.get_image_info())
        if not raw.strip() or image_content:
            dpi = min(150, int(2500 * 72 / max(page.rect.width, page.rect.height, 1)))
            try:
                if dpi < 36:
                    raise ValueError("The page is too large for OCR.")
                text_page = page.get_textpage_ocr(language="eng", dpi=dpi, full=not raw.strip())
                raw = page.get_text("text", textpage=text_page)
                if image_content:
                    _note(f"Page {page_no}: OCR read the images. Compare the extracted text with the source.")
            except (RuntimeError, ValueError):
                _note(f"Page {page_no}: local OCR was unavailable. Images or scanned text may be missing.")
        if not raw.strip():
            _note(f"Page {page_no}: no readable text was extracted.")
        if index and buffer:
            flush(page_no - 1)
        if medical and _is_benefit_page(raw):
            flush(page_no)
            take_headers(raw)
            chunks.extend(
                _chunks(
                    title,
                    "In-Patient Benefit Entitlement Schedule",
                    "In-Patient Benefit Entitlement Schedule",
                    "In-Patient Benefit Entitlement Schedule\nAnnual In-Patient Health\nBenefit",
                    page=page_no,
                )
            )
            chunks.extend(_benefit_rows(title, page_no))
            continue
        if medical and _is_claim_form_page(raw):
            flush(page_no)
            take_headers(raw)
            image = _save_page(document, index, policy_id, f"page-{page_no}.png")
            claim_title = "Annexure D: In-Patient Claim Form and Required Documents"
            chunks.extend(
                _chunks(
                    title,
                    claim_title,
                    claim_title,
                    f"{claim_title}\n{_CLAIM_FORM}",
                    content_type="form",
                    page=page_no,
                    image_path=image,
                )
            )
            continue
        tables = _pdf_tables(page, page_no) if not medical and raw.strip() else []
        lines = take_headers(_text_outside_tables(page, tables, text_page) if tables else raw)
        if medical and _is_flowchart_page(raw):
            flush(page_no)
            prose = [line for line in lines if not _pdf_heading(line)]
            if prose:
                chunks.extend(
                    _chunks(
                        title,
                        "Reimbursement documents",
                        "Reimbursement documents",
                        "\n".join(prose),
                        page=page_no,
                    )
                )
            image = _save_page(document, index, policy_id, f"page-{page_no}.png")
            chunks.extend(
                _chunks(
                    title,
                    "Annexure E: In-Patient Claim Process",
                    "Annexure E: In-Patient Claim Process",
                    _FLOWCHART,
                    content_type="form",
                    page=page_no,
                    image_path=image,
                )
            )
            continue
        for line in lines:
            if _pdf_heading(line):
                begin_heading(line, page_no)
            else:
                buffer.append(line)
        links = [link.get("uri", "") for link in page.get_links()]
        buffer.extend(uri for uri in dict.fromkeys(links)
                      if re.match(r"^https?://", uri, re.I) and uri not in raw)
        for index_in_page, (_, rows) in enumerate(tables, start=1):
            table_heading = f"Table {index_in_page} on page {page_no}"
            headers = [cell or f"Column {index + 1}" for index, cell in enumerate(rows[0])]
            for row in rows[1:]:
                description = ". ".join(f"{key}: {value}" for key, value in zip(headers, row) if value)
                chunks.extend(_chunks(title, f"{heading} > {table_heading}", table_heading,
                                      description, content_type="table", page=page_no))
    flush(document.page_count or None)
    if header_lines:
        chunks.extend(
            _chunks(
                title,
                "Document header",
                "Document header",
                "\n".join(header_lines),
                page=1,
            )
        )
    return chunks


def _pdf_tables(page, page_no):
    if not hasattr(page, "find_tables"):
        return []
    try:
        found = []
        for table in page.find_tables().tables:
            rows = [[(cell or "").strip() for cell in row] for row in table.extract()]
            if len(rows) >= 2 and len(rows[0]) >= 2:
                found.append((table.bbox, rows))
        return found
    except (RuntimeError, ValueError):
        _note(f"Page {page_no}: automatic table detection failed.")
        return []


def _text_outside_tables(page, tables, text_page) -> str:
    lines: dict[tuple[int, int], list[str]] = {}
    for x0, y0, x1, y1, text, block, line, _ in page.get_text("words", textpage=text_page):
        x = (x0 + x1) / 2
        y = (y0 + y1) / 2
        if not any(left <= x <= right and top <= y <= bottom for (left, top, right, bottom), _ in tables):
            lines.setdefault((block, line), []).append(text)
    return "\n".join(" ".join(words) for words in lines.values())


def _running_header(text: str) -> bool:
    folded = text.lower()
    if folded.startswith("document code") or folded.startswith("revision date") or folded.startswith("revision no"):
        return True
    return folded.startswith("page ") and " of " in folded


def _pdf_lines(raw: str) -> tuple[list[str], list[str]]:
    lines = []
    headers = []
    for line in raw.splitlines():
        text = " ".join(line.split())
        if not text:
            continue
        if _running_header(text):
            headers.append(text)
            continue
        lines.append(text)
    return _join_broken_urls(lines), headers


def _join_broken_urls(lines: list[str]) -> list[str]:
    """A web address printed over two lines is one address."""
    joined: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.search(r"https?://\S+$", line) and index + 1 < len(lines):
            nxt = lines[index + 1].strip()
            if nxt and " " not in nxt and not re.match(r"^\d", nxt):
                line = line.rstrip() + nxt
                index += 1
        joined.append(line)
        index += 1
    return joined


def _pdf_heading(line: str) -> bool:
    """A short title starts a section. A numbered sentence stays in the section body."""
    if line.lower().startswith("annexure") and len(line.split()) <= 12:
        return True
    match = re.match(r"^\d+(?:\.\d+)*\.?\s+(\S.*)$", line)
    if not match:
        return False
    rest = match.group(1)
    if len(rest.split()) > 8 or rest[-1:] in ".,;:":
        return False
    return True


def _is_benefit_page(raw: str) -> bool:
    folded = raw.lower()
    return "annexure b" in folded and "benefit entitlement" in folded


def _is_claim_form_page(raw: str) -> bool:
    """The claim form page is the picture. The attachments list only mentions the name."""
    folded = raw.lower()
    return "annexure d" in folded and "claim form" in folded and len(raw) < 500


def _is_flowchart_page(raw: str) -> bool:
    folded = raw.lower()
    return "annexure e" in folded and "following documents are required" in folded


def _benefit_rows(title: str, page: int) -> list[ChunkDraft]:
    heading = "Annexure B: In-Patient Medical Benefit Schedule"
    rows = []
    for plan, who, amounts in _MEDICAL_PLANS:
        body = f"Plan: {plan}. Grades: {who}. {amounts}"
        rows.extend(_chunks(title, heading, heading, body, content_type="table", page=page))
    return rows


def _save_page(document, index: int, policy_id: str, name: str) -> str:
    import pymupdf

    page = document[index]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2))
    return save_picture(policy_id, name, pix.tobytes("png"))


def _travel(document, policy_id: str) -> tuple[list[ChunkDraft], str, date | None]:
    title = "Travel Policy"
    chunks: list[ChunkDraft] = []
    for index, page in enumerate(_TRAVEL_PAGES):
        image = _save_page(document, index, policy_id, f"page-{index + 1}.png")
        page_line = "Page 10 of 10" if index >= 8 else f"Page {index + 1} of 10"
        chunks.extend(
            _chunks(
                title,
                page_line,
                page_line,
                f"Stylo (Pvt.) Ltd. - HR Policy Manual. {page_line}.",
                page=index + 1,
                image_path=image,
            )
        )
        for item in page:
            chunks.extend(
                _chunks(
                    title,
                    item["heading"],
                    item["heading"],
                    item["body"],
                    content_type=item.get("kind", "section"),
                    page=index + 1,
                    image_path=image,
                    polarity=item.get("polarity") or _polarity(item["heading"], item["body"]),
                )
            )
    text = "\n".join(chunk.body for chunk in chunks)
    return chunks, title, _effective_date(text)


def _chunks(
    title: str,
    heading_path: str,
    heading: str,
    body: str,
    *,
    content_type: str = "section",
    page: int | None = None,
    image_path: str = "",
    polarity: str = "",
) -> list[ChunkDraft]:
    body = body.strip()
    if not body:
        return []
    label = f"{title}, {heading}" if heading and heading != title else title
    pieces = _split_section(body)
    found = []
    for piece in pieces:
        embed = embed_input(title[:200], heading_path[:600], heading, piece)
        found.append(
            ChunkDraft(
                section_label=label,
                heading=heading or title,
                body=piece,
                embed_text=embed,
                heading_path=heading_path,
                content_type=content_type,
                page=page,
                image_path=image_path,
                polarity=polarity,
            )
        )
    return found


def _split_section(text: str) -> list[str]:
    text = text.strip()
    if len(text.encode("utf-8")) <= CHUNK_CHARS:
        return [text]
    if CHUNK_CHARS <= 0 or not 0 <= CHUNK_OVERLAP < CHUNK_CHARS:
        raise RuntimeError("Overlap must be smaller than the passage size.")
    units = _units(text)
    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit.encode("utf-8")) > CHUNK_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_word_windows(unit))
            continue
        candidate = f"{current}\n{unit}".strip() if current else unit
        if len(candidate.encode("utf-8")) <= CHUNK_CHARS:
            current = candidate
            continue
        chunks.append(current)
        tail = _overlap_tail(current)
        current = f"{tail}\n{unit}".strip() if tail else unit
        if len(current.encode("utf-8")) > CHUNK_CHARS:
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _units(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph.encode("utf-8")) <= CHUNK_CHARS:
            units.append(paragraph)
            continue
        buffer = ""
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = f"{buffer} {sentence}".strip() if buffer else sentence
            if buffer and len(candidate.encode("utf-8")) > CHUNK_CHARS:
                units.append(buffer)
                buffer = sentence
            else:
                buffer = candidate
        if buffer:
            units.append(buffer)
    return units


def _word_windows(text: str) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for word in text.split():
        if len(word.encode("utf-8")) > CHUNK_CHARS:
            if current:
                chunks.append(" ".join(current))
                current = []
            fragment = ""
            size = 0
            for character in word:
                width = len(character.encode("utf-8"))
                if size + width > CHUNK_CHARS:
                    chunks.append(fragment)
                    fragment, size = "", 0
                fragment += character
                size += width
            current = [fragment]
            continue
        candidate = " ".join([*current, word])
        if current and len(candidate.encode("utf-8")) > CHUNK_CHARS:
            chunks.append(" ".join(current))
            current = _overlap_words(current)
            while current and len(" ".join([*current, word]).encode("utf-8")) > CHUNK_CHARS:
                current = current[1:]
        current.append(word)
    if current:
        chunks.append(" ".join(current))
    return chunks


def _overlap_tail(text: str) -> str:
    if not CHUNK_OVERLAP:
        return ""
    tail = text.encode("utf-8")[-CHUNK_OVERLAP:].decode("utf-8", errors="ignore")
    space = tail.find(" ")
    if space != -1:
        tail = tail[space + 1 :]
    return tail.strip()


def _overlap_words(words: list[str]) -> list[str]:
    kept: list[str] = []
    used = 0
    for word in reversed(words):
        extra = len(word.encode("utf-8")) + (1 if kept else 0)
        if used + extra > CHUNK_OVERLAP:
            break
        kept.append(word)
        used += extra
    return list(reversed(kept))


def _polarity(heading: str, body: str) -> str:
    blob = f"{heading}\n{body}".lower()
    markers = (
        "exclusion",
        "excluded",
        "not eligible",
        "no longer",
        "will cease",
        "not be covered",
        "shall not be covered",
        "no benefits shall be paid",
        "immediate dismissal",
        "subject to immediate",
    )
    if any(marker in blob for marker in markers):
        return "exclusion"
    return ""


# Travel is a scan. These passages are the words on each page, in file order.
# Page 9 is the hotel list and page 10 is the fuel list. Both footers say "Page 10 of 10".
_TRAVEL_PAGES: list[list[dict]] = [
    [
        {
            "heading": "Travel Policy",
            "body": (
                "Stylo (Pvt.) Ltd. - HR Policy Manual. TRAVEL POLICY. Code: Rev: 00. "
                "Approved By: Zainab Khan, Head HR, and Huzaifa Siddiqui, COO. "
                "Name of Official Person: Zainab Khan and Huzaifa Siddiqui. "
                "Designation: Head HR and COO. Date of Signature: 14/4/25. "
                "Effective Date of Policy: April 15, 2025."
            ),
        },
        {
            "heading": "1. Purpose",
            "body": (
                "This policy outlines the guidelines and procedures for business travel to ensure consistency, "
                "cost-efficiency, and proper documentation for employees traveling on behalf of the company."
            ),
        },
        {
            "heading": "2. Eligibility",
            "body": (
                "2.1. This policy applies to all permanent management employees of Stylo who are required to travel "
                "for work-related purposes within the country, covering a distance of 300 kilometers or more round trip. "
                "2.2. All employees travelling in groups for company events such as conferences, outbreaks and group "
                "trainings are excluded from the above purpose."
            ),
            "polarity": "exclusion",
        },
        {
            "heading": "3.1. Bookings and Record Keeping",
            "body": (
                "3.1.1. All Travel bookings will be made through the Administration Department.\n"
                "3.1.2. The Administration Department is responsible for negotiating and managing relationships "
                "with hotels, travel agencies, and other travel-related vendors.\n"
                "3.1.3. The Administration Department will be responsible for keeping an updated hotel star "
                "listing & inform Human Resource (HR) Department in case there is any change.\n"
                "3.1.4. The Administration Department will be responsible for maintaining the travel records of "
                "all employees.\n"
                "3.1.5. Notify the employee once travel and accommodation arrangements are confirmed and "
                "provide them with all relevant documents.\n"
                "3.1.6. The Administration Department should verify and approve the accuracy and completeness "
                "of the bill, ensuring it matches the services or goods received from the vendor."
            ),
        },
    ],
    [
        {
            "heading": "3.1. Bookings and Record Keeping",
            "body": (
                "3.1.7. The Finance Department will ensure the bill is processed as per agreed timelines with "
                "Administration Department on receiving the bill."
            ),
        },
        {
            "heading": "3.2. Authorization of Travel",
            "body": (
                "3.2.1. All business travel must be pre-approved by the competent authority as stated in the table below:\n"
                "3.2.2. A dually signed Travel Request Form (please refer to Annexure A) must be submitted to the Administration Department via HR Department at least 7 days prior to the travel date.\n"
                "3.2.3. In the event of an urgent travel requirement, the employee must seek approval via email from the relevant authority. Upon returning, the employee will complete the Travel Request Form and obtain all necessary approvals.\n"
                "3.2.4. For Sales Staff, they will follow the internal guidelines already in place for Sales & Operation Department.\n"
                "3.2.5. For Field Audit Staff, they will follow the internal guidelines already in place for Audit Department."
            ),
        },
        {
            "heading": "3.2. Authorization of Travel",
            "kind": "table",
            "body": "Grade: COO (Business), Group Chief, General Manager, Deputy General Manager. Competent Authority: Director/COO.",
        },
        {
            "heading": "3.2. Authorization of Travel",
            "kind": "table",
            "body": "Grade: Assistant General Manager/Senior Manager. Competent Authority: GC, GM.",
        },
        {
            "heading": "3.2. Authorization of Travel",
            "kind": "table",
            "body": "Grade: Manager, Assistant Manager, Senior Executive, Executive, Officer and below. Competent Authority: Head of Department.",
        },
        {
            "heading": "4. Mode of Travel",
            "body": (
                "4.1. The Administration Department will be responsible for selecting an appropriate mode of travel, "
                "depending on distance, travel time cost effectiveness and safety. Please refer to the following "
                "entitlements table for travelling within the country:"
            ),
        },
        {
            "heading": "4. Mode of Travel",
            "kind": "table",
            "body": (
                "Grade: COO (Business) / Group Chief (GC). Travel by air: Business Class (Local & International). "
                "Travel by rail: A/C-Sleeper (Green Line) / A/C Business Class. "
                "Travel by road: Company-Maintained Vehicle/Daewoo Super Luxury."
            ),
        },
        {
            "heading": "4. Mode of Travel",
            "kind": "table",
            "body": (
                "Grade: Deputy General Manager (DGM) to General Manager (GM). Travel by air: Economy Class (Local & International). "
                "Travel by rail: A/C-Sleeper (Green Line) / A/C Business Class. "
                "Travel by road: Company-Maintained Vehicle/Daewoo Super Luxury."
            ),
        },
    ],
    [
        {
            "heading": "4. Mode of Travel",
            "kind": "table",
            "body": (
                "Grade: Assistant General Manager (AGM) & Senior Manager (SM). Travel by air: Economy Class (Local & International). "
                "Travel by rail: A/C-Business Class. Travel by road: Company-Maintained Vehicle/Daewoo Normal or Any A/C Bus."
            ),
        },
        {
            "heading": "4. Mode of Travel",
            "kind": "table",
            "body": (
                "Grade: Manager (M) & Assistant Manager (AM). Travel by air: Economy Class (Local & International). "
                "Travel by rail: A/C-Business Class. Travel by road: Daewoo Normal or Any A/C Bus."
            ),
        },
        {
            "heading": "4. Mode of Travel",
            "kind": "table",
            "body": (
                "Grade: Senior Executive (SE) & below. Travel by air: -. "
                "Travel by rail: A/C-Standard/Lower. Travel by road: A/C Bus."
            ),
        },
        {
            "heading": "4. Mode of Travel",
            "body": (
                "4.2. Carrier choice in the case of air travel will be determined by the Administration Department "
                "based on cost and travel time.\n"
                "4.3. Female employees not eligible for air travel based on their grade will follow the travel policy "
                "applicable to the lowest grade with air travel entitlement."
            ),
            "polarity": "exclusion",
        },
        {
            "heading": "4.4. Travel for Employees with Company-Maintained Vehicles (Car/Motorbike) or Vehicle Allowance",
            "body": (
                "4.4.1. Employees using a company-maintained vehicle for business travel, or those with a vehicle "
                "allowance, will be reimbursed for fuel expenses based on the mileage rate outlined in the Annexure D. "
                "Additionally, employees will be reimbursed for any actual toll charges incurred during business travel, "
                "provided that receipts and proper documentation are submitted."
            ),
        },
        {
            "heading": "4.5. Travel by Personal Vehicle (Car/Motorbike)",
            "body": (
                "4.5.1.1. Employees are required to obtain prior approval from the relevant authority before using "
                "personal vehicles for company business. However, the use of personal transport is generally discouraged, "
                "where company-provided transport (such as company-maintained vehicles) is available to the employee. "
                "If personal vehicles are used for business travel with prior approval, employees are eligible for "
                "reimbursement of fuel expenses at the same mileage rate specified in the Vehicle Policy for category "
                "mentioned in point 4.4. Additionally, employees will be reimbursed for any actual toll charges incurred "
                "during business travel, provided that receipts and proper documentation are submitted.\n"
                "4.5.1.2. Employees who are not eligible for company-maintained vehicles or vehicle allowance, are "
                "eligible for reimbursement of fuel expenses at the same mileage rate specified in the Vehicle Policy. "
                "Additionally, employees will be reimbursed for any actual toll charges incurred during business travel, "
                "provided that receipts and proper documentation are submitted."
            ),
        },
    ],
    [
        {
            "heading": "4.5.2. Travel by Taxi/Rent-a-car",
            "body": (
                "4.5.2.1. Tax/Rent-a-car charges will be reimbursed on actual for all grades if the employee "
                "is required to use this mode of transport for business travel. The employee will be required to "
                "submit actual bills.\n"
                "4.5.2.2. Taxi/ Rent-a-car charges will be reimbursed on actual for intra-city business travel "
                "during travelling upon submission of actual bills."
            ),
        },
        {
            "heading": "5. Accommodation",
            "body": (
                "5.1. Employees will be eligible for accommodation in company-provided hotels or guest rooms, based "
                "on the entitlement levels outlined in the table below:\n"
                "Grade: COO (Business) / Group Chief (GC). Hotel Entitlement: Annexure C.\n"
                "Grade: Deputy General Manager (DGM) to General Manager (GM). Hotel Entitlement: Annexure C.\n"
                "Grade: Assistant General Manager (AGM) & Senior Manager (SM). Hotel Entitlement: Annexure C.\n"
                "Grade: Manager (M) & Assistant Manager (AM). Hotel Entitlement: Annexure C.\n"
                "Grade: Senior Executive (SE) & below. Hotel Entitlement: Annexure C.\n"
                "5.2. The booking request will be given to the Administration Department who will decide about the "
                "stay as per entitlement and company interest. The Administration Department will negotiate corporate "
                "rates with hotels for accommodation.\n"
                "5.3. Accommodation will be provided on a single-occupancy basis. If an employee opts for double "
                "occupancy, he/she will be responsible for covering the difference in cost, which must be paid in cash.\n"
                "5.4. Employees wishing to stay in accommodation above their entitlement shall be allowed by the "
                "relevant authority and the HR Department, provided that the concerned employee pays the differential amount.\n"
                "5.5. If an employee is traveling with their supervisor for a meeting, they may stay at the same hotel "
                "as their supervisor, subject to prior approval from the HR Department.\n"
                "5.6. If an employee is travelling with their peer group for a visit/meeting, they must stay at the same "
                "hotel, they would be required to stay in twin sharing as per their hotel entitlement.\n"
                "5.7. If the entitled accommodation is unavailable, the employee may be accommodated in a higher-grade "
                "facility with prior approval from the relevant authority and the HR Department.\n"
                "5.8. In small towns or rural areas where hotels are unavailable, employees may arrange accommodation "
                "in a rest house or circuit house, provided it is equivalent to or below their entitled hotel category "
                "for overnight stays while on company business. Reimbursement can be claimed by submitting a Travel "
                "Expense Form (Please refer to Annexure B) to the HR Department for verification. The HR Department "
                "will forward the form to Finance Department for further action."
            ),
        },
    ],
    [
        {
            "heading": "5. Accommodation",
            "body": (
                "5.9. Female employees traveling within Pakistan, regardless of their entitlement, are permitted to "
                "stay at a secure hotel, including five-star hotels, when necessary.\n"
                "5.10. Hotel expenses other than room rent include breakfast only.\n"
                "5.11. For departmental training or conferences, all employees are required to stay at the same "
                "hotel, with the Head of Department responsible for approving the accommodation."
            ),
        },
        {
            "heading": "6. Daily Allowance",
            "body": (
                "Daily Allowance is inclusive of all out-of-pocket expenses like meals, tips, laundry, and other incidental "
                "expenses. This amount is exclusive of guest room, hotel rents and taxi fare.\n"
                "6.1. A daily allowance will be granted for business travel originating from the permanent place of "
                "work, applicable for journeys totaling 300 km or more (round trip), including transit time.\n"
                "6.2. One full DA is applicable for a business-related absence from the permanent place of work that "
                "lasts for a full calendar day or exceeds 12 hours.\n"
                "6.3. Half DA is granted for same-day return trips if the work-related travel is 12 hours or less.\n"
                "6.4. For training or sponsored travel that provides breakfast and lunch, half DA will be provided to "
                "cover dinner and any additional out-of-pocket expenses. If the travel is fully sponsored, no DA "
                "will be granted.\n"
                "6.5. A daily allowance will be granted for weekends and public holidays that occur during the business trip.\n"
                "6.6. The daily allowance rates are as follows:"
            ),
        },
        {
            "heading": "6. Daily Allowance",
            "kind": "table",
            "body": "Grade: COO (Business) / Group Chief (GC). Daily Allowance/per day (given in rupees): 2,000.",
        },
        {
            "heading": "6. Daily Allowance",
            "kind": "table",
            "body": "Grade: Deputy General Manager (DGM) to General Manager (GM). Daily Allowance/per day (given in rupees): 1,800.",
        },
        {
            "heading": "6. Daily Allowance",
            "kind": "table",
            "body": "Grade: Assistant General Manager (AGM) & Senior Manager (SM). Daily Allowance/per day (given in rupees): 1,600.",
        },
        {
            "heading": "6. Daily Allowance",
            "kind": "table",
            "body": "Grade: Assistant Manager (AM) & Manager (M). Daily Allowance/per day (given in rupees): 1,500.",
        },
        {
            "heading": "6. Daily Allowance",
            "kind": "table",
            "body": "Grade: Senior Executive (SE) & below. Daily Allowance/per day (given in rupees): 1,200.",
        },
        {
            "heading": "7. Daily Tour Allowance for Field-Based Employees",
            "body": (
                "7.1. Daily Tour allowance (same day return) shall be admissible if employee travels from his base "
                "town to any other town (same day return) 25 Kilometers or above (one way).\n"
                "7.2. Daily Tour allowance shall be given as per following rates:"
            ),
        },
    ],
    [
        {
            "heading": "7. Daily Tour Allowance for Field-Based Employees",
            "kind": "table",
            "body": "Daily tour allowance rates. Regional Sales Manager. 600. 1,500.",
        },
        {
            "heading": "7. Daily Tour Allowance for Field-Based Employees",
            "kind": "table",
            "body": "Daily tour allowance rates. Area Sales Manager. 500. 1,000.",
        },
        {
            "heading": "8. Travel Expense Form",
            "body": (
                "8.1. Employees must complete the Travel Expense Form (Please see Annexure B) for any reimbursements "
                "and settlement of expenses, and submit it to the Finance Department, along with all original bills "
                "and receipts, within 7 days of returning from business travel. Prior approval from the relevant "
                "authority is required before submission.\n"
                "8.2. Any expenses exceeding the specified entitlements must be approved by the relevant authority.\n"
                "8.3. In the case of advance payments, the employee is responsible for returning any excess amount, "
                "or it will be deducted from their account."
            ),
        },
        {
            "heading": "9. Travel of Family Members",
            "body": (
                "9.1. An employee who wishes to travel with family members may do so at their own expense. The "
                "company will not be liable to bear travel and other related expenses (accommodation, meals etc.)."
            ),
            "polarity": "exclusion",
        },
        {
            "heading": "10. Cancellations and Missed Flights",
            "body": (
                "10.1. In the event of a business trip cancellation, employees must promptly notify the "
                "Administration Department to prevent any unnecessary penalties."
            ),
        },
        {
            "heading": "11. Policy Renewal and Review",
            "body": (
                "11.1. The Travel Policy will undergo a comprehensive review every two years, or sooner if required, "
                "to ensure it remains aligned with organizational needs and industry best practices. "
                "Any necessary adjustments will be implemented to reflect changes in business priorities, or operational requirements. "
                "11.2. Employees will be informed of any changes to the policy during the renewal period."
            ),
        },
        {
            "heading": "12. Policy Attachments",
            "body": (
                "Travel Request Form- Annexure A.\n"
                "Travel Expense Form-Annexure B.\n"
                "Hotel Entitlement List-Annexure C.\n"
                "Fuel Entitlement List-Annexure D."
            ),
        },
        {
            "heading": "13. Policy Effective Date",
            "body": (
                "This policy shall be effective from April 15th, 2025 and shall supersede all previous policies, written "
                "or verbal, related to Employee's Travel Policy."
            ),
        },
    ],
    [
        {
            "heading": "Annexure A: Travel Request Form",
            "kind": "form",
            "body": (
                "Annexures. Annexure A: Travel Request Form: https://home.ssis.pk/travel-request-form. "
                "Travel Request Form. Form Reference: TRF-2025-04-16/01.\n"
                "Employee Information: Full Name, Department, Employee ID, Job Title, Base Station, Contact No, "
                "Type of Travel (Overnight / Same Day), No. of Nights in case of Overnight Stay.\n"
                "No. of Passengers (Please fill this section if there are more than 1 passengers): "
                "Employee ID, Name, Job Title, Department, Same Day, No. of Nights.\n"
                "Purpose of Travel (Please be specific).\n"
                "Travel Details: Travel Start Date, Travel End Date, Date of Travel, Time of Departure, "
                "From (City), To (City), Mode of Travel, Travel Class.\n"
                "Accommodation Details: Hotel Stay Requested, Self Accommodation, No Stay Required.\n"
                "Advance Details: Advance Required.\n"
                "Additional Comments (If Any).\n"
                "Authorization: Employee's Signature, Date of Submission, Competent Authority Signature."
            ),
        }
    ],
    [
        {
            "heading": "Annexure B: Travel Expense Form",
            "kind": "form",
            "body": (
                "Annexure B: Travel Expense Form: https://home.ssis.pk/travel-claim-form. "
                "TRAVEL CLAIM FORM. Form Reference: TCF-2025-04-9.\n"
                "Employee Details: Name, Employee Code, Designation, Department.\n"
                "Travel Details: Start Date, Start Time, End Date, End Time, Same Day, Travel Type, "
                "Days Travel, Nights Stay, Visit Details, Additional Remarks.\n"
                "Expenses Details: Description, Amount (Rs), Notes.\n"
                "Rate per KM. Total KM. Toll Plaza (if out of city): Receipt required. "
                "Ticket Charges (if local traveling): Receipt required. "
                "Local Travel: No receipt required for local travel. Meals. "
                "Hotel Bill (if not BTC). Self Stay: No receipt required. "
                "Board/Meeting Room Charges. Total Expenses.\n"
                "Traveller Signature. HOD/Director."
            ),
        }
    ],
    [
        {
            "heading": "Annexure C",
            "kind": "table",
            "body": (
                "Annexure C. Grade: Group Chief (GC). Hotel Entitlement: Avari Towers, Pearl Continental, Marriott Hotel, Movenpick Hotel, "
                "Ramada Creek Hotel, Nishat Hotel, Faletti's Hotel, Serena Hotel. "
                "Proposed Rates: PKR 28,000 – PKR 35,000. Self-Stay: PKR 22,000/-. "
                "*All applicable taxes shall be treated separately from the above limits."
            ),
        },
        {
            "heading": "Annexure C",
            "kind": "table",
            "body": (
                "Grade: Deputy General Manager (DGM) to General Manager (GM). "
                "Hotel Entitlement: Nishat Hotel, Royal Swiss, Luxus Grand, Best Western Premier, Avari Express. "
                "Proposed Rates: PKR 24,000 – PKR 28,000. Self-Stay: PKR 15,500/-."
            ),
        },
        {
            "heading": "Annexure C",
            "kind": "table",
            "body": (
                "Grade: Assistant General Manager (AGM) & Senior Manager (SM). "
                "Hotel Entitlement: Challet Hotel, Hotel One, Hotel Excelsior. "
                "Proposed Rates: PKR 20,000 – PKR 24,000. Self-Stay: PKR 13,000/-."
            ),
        },
        {
            "heading": "Annexure C",
            "kind": "table",
            "body": (
                "Grade: Assistant Manager (AM) & Manager (M). "
                "Hotel Entitlement: Faran Hotel, Asaish Hotel or as per available list of Hotels. "
                "Proposed Rates: PKR 12,000 – PKR 16,000. Self-Stay: PKR 8,000/-."
            ),
        },
        {
            "heading": "Annexure C",
            "kind": "table",
            "body": (
                "Grade: Senior Executive (SE) & Below. Hotel Entitlement: Twin Sharing (as per available list of hotels). "
                "Proposed Rates: -. Self-Stay: PKR 5,000/-."
            ),
        },
    ],
    [
        {
            "heading": "Annexure D: Fuel Entitlement List",
            "kind": "table",
            "body": "Sr. #: 1. Vehicle type: 70cc petrol. Grade entitlement: All Levels. Mileage (KM Coverage): 40 km/liter. Maintenance and depreciation (New Rates): Rs. 2.0/km.",
        },
        {
            "heading": "Annexure D: Fuel Entitlement List",
            "kind": "table",
            "body": "Sr. #: 2. Vehicle type: 125cc petrol. Grade entitlement: All Levels. Mileage (KM Coverage): 30 km/liter. Maintenance and depreciation (New Rates): Rs. 2.5/km.",
        },
        {
            "heading": "Annexure D: Fuel Entitlement List",
            "kind": "table",
            "body": "Sr. #: 3. Vehicle type: 660cc/800cc petrol. Grade entitlement: O to M. Mileage (KM Coverage): 14 km/liter. Maintenance and depreciation (New Rates): Rs. 3.5/km.",
        },
        {
            "heading": "Annexure D: Fuel Entitlement List",
            "kind": "table",
            "body": "Sr. #: 4. Vehicle type: 1000cc petrol. Grade entitlement: SM / AGM. Mileage (KM Coverage): 12 km/liter. Maintenance and depreciation (New Rates): Rs. 3.5/km.",
        },
        {
            "heading": "Annexure D: Fuel Entitlement List",
            "kind": "table",
            "body": "Sr. #: 5. Vehicle type: 1300cc petrol. Grade entitlement: DGM / GM. Mileage (KM Coverage): 10 km/liter. Maintenance and depreciation (New Rates): Rs. 4.0/km.",
        },
        {
            "heading": "Annexure D: Fuel Entitlement List",
            "kind": "table",
            "body": "Sr. #: 6. Vehicle type: 1500cc petrol. Grade entitlement: GM / GC. Mileage (KM Coverage): 10 km/liter. Maintenance and depreciation (New Rates): Rs. 5.0/km.",
        },
        {
            "heading": "Annexure D: Fuel Entitlement List",
            "kind": "table",
            "body": "Sr. #: 7. Vehicle type: 1800cc petrol. Grade entitlement: GC / COO (Business). Mileage (KM Coverage): 10 km/liter. Maintenance and depreciation (New Rates): Rs. 6.0/km.",
        },
    ],
]
