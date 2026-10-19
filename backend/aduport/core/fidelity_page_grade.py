"""Page-level fidelity grades.

The 630-cell matrix is a regression rate on synthetic seeds. This scale is a
different ruler: a person compares each source page with the conversion and
assigns one grade. Those grades are not averaged into a success percentage.

``CORPUS_READ`` stays false until 20–30 real business documents have a per-page
grade list. Calibration grades on in-repo fixtures may be stored separately
under ``kind="calibration"`` and never flip that flag.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PageGrade = Literal["A", "B", "C", "D"]
CorpusKind = Literal["business", "calibration"]

# Worst applicable check wins. A missing figure on a figure page is not "99%".
PAGE_GRADES: dict[str, str] = {
    "A": "这一页的意思、结构和阅读顺序都在；样式漂移可以接受",
    "B": "意思还在；表格、标题、列表或图有降级，但读者找得到",
    "C": "读者会漏掉或读错这一页上的内容",
    "D": "这一页不可用：空白、错乱，或不是这一页的内容",
}

PAGE_CHECKS: tuple[str, ...] = (
    "reading_order",
    "headings",
    "body_text",
    "lists",
    "tables",
    "figures_and_captions",
    "headers_footers",
    "footnotes_comments",
    "formulas",
)

CORPUS_MIN = 20
CORPUS_MAX = 30
CORPUS_READ = False

_FORBIDDEN_SUMMARY_KEYS = frozenset(
    {
        "success_rate",
        "pass_rate",
        "accuracy",
        "成功率",
        "通过率",
    }
)


@dataclass
class PageGradeRow:
    """One page (or slide) judgment."""

    page: int
    grade: PageGrade
    notes: str = ""
    checks_failed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentGradeSheet:
    """Worksheet for one source document through one target."""

    doc_id: str
    source_path: str
    source_format: str
    target_format: str
    kind: CorpusKind = "business"
    pages: list[PageGradeRow] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "source_path": self.source_path,
            "source_format": self.source_format,
            "target_format": self.target_format,
            "kind": self.kind,
            "notes": self.notes,
            "pages": [row.to_dict() for row in self.pages],
        }


@dataclass
class GradeSummary:
    """Distribution of page grades. No success rate field exists on purpose."""

    kind: CorpusKind
    documents: int
    pages: int
    by_grade: dict[str, int]
    worst: PageGrade | None
    documents_with_c_or_worse: int
    corpus_read: bool
    ready_for_business_claim: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in _FORBIDDEN_SUMMARY_KEYS:
            payload.pop(key, None)
        return payload


def assert_grade_list(grades: list[str]) -> None:
    """Accept only the four page grades. Reject a numeric success rate."""
    unknown = [grade for grade in grades if grade not in PAGE_GRADES]
    if unknown:
        raise ValueError(f"unknown page grade: {unknown[0]}")
    if not CORPUS_READ and grades:
        raise ValueError("no business-document page read yet; refusing to record grades")


def validate_page_grade(grade: str) -> PageGrade:
    if grade not in PAGE_GRADES:
        raise ValueError(f"unknown page grade: {grade}")
    return grade  # type: ignore[return-value]


def document_worst(pages: list[PageGradeRow]) -> PageGrade | None:
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    if not pages:
        return None
    return max((row.grade for row in pages), key=lambda g: order[g])


def summarize_sheets(sheets: list[DocumentGradeSheet]) -> GradeSummary:
    """Aggregate page grades. Refuses to invent a success percentage."""
    if not sheets:
        return GradeSummary(
            kind="calibration",
            documents=0,
            pages=0,
            by_grade={"A": 0, "B": 0, "C": 0, "D": 0},
            worst=None,
            documents_with_c_or_worse=0,
            corpus_read=CORPUS_READ,
            ready_for_business_claim=False,
        )
    kinds = {sheet.kind for sheet in sheets}
    if len(kinds) != 1:
        raise ValueError("cannot mix business and calibration sheets in one summary")
    kind: CorpusKind = kinds.pop()  # type: ignore[assignment]
    counts: Counter[str] = Counter()
    bad_docs = 0
    for sheet in sheets:
        if not sheet.pages:
            continue
        for row in sheet.pages:
            validate_page_grade(row.grade)
            counts[row.grade] += 1
        worst = document_worst(sheet.pages)
        if worst in {"C", "D"}:
            bad_docs += 1
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    present = [g for g in ("A", "B", "C", "D") if counts[g]]
    worst_all: PageGrade | None = (
        max(present, key=lambda g: order[g]) if present else None  # type: ignore[arg-type]
    )
    docs_graded = sum(1 for sheet in sheets if sheet.pages)
    pages = sum(counts.values())
    ready = (
        kind == "business"
        and CORPUS_READ
        and CORPUS_MIN <= docs_graded <= CORPUS_MAX
        and pages > 0
    )
    return GradeSummary(
        kind=kind,
        documents=docs_graded,
        pages=pages,
        by_grade={g: int(counts.get(g, 0)) for g in ("A", "B", "C", "D")},
        worst=worst_all,
        documents_with_c_or_worse=bad_docs,
        corpus_read=CORPUS_READ,
        ready_for_business_claim=ready,
    )


def sheets_from_payload(payload: dict[str, Any]) -> list[DocumentGradeSheet]:
    """Load worksheets. Rejects files that smuggle a success-rate field."""
    for key in _FORBIDDEN_SUMMARY_KEYS:
        if key in payload:
            raise ValueError(f"page-grade worksheets must not contain {key!r}")
    out: list[DocumentGradeSheet] = []
    for raw in payload.get("documents") or []:
        if not isinstance(raw, dict):
            continue
        for key in _FORBIDDEN_SUMMARY_KEYS:
            if key in raw:
                raise ValueError(f"document sheet must not contain {key!r}")
        pages: list[PageGradeRow] = []
        for page in raw.get("pages") or []:
            if not isinstance(page, dict):
                continue
            grade = page.get("grade")
            if grade in (None, ""):
                continue
            pages.append(
                PageGradeRow(
                    page=int(page.get("page") or 0),
                    grade=validate_page_grade(str(grade)),
                    notes=str(page.get("notes") or ""),
                    checks_failed=[
                        str(item) for item in (page.get("checks_failed") or []) if item
                    ],
                )
            )
        out.append(
            DocumentGradeSheet(
                doc_id=str(raw.get("doc_id") or ""),
                source_path=str(raw.get("source_path") or ""),
                source_format=str(raw.get("source_format") or ""),
                target_format=str(raw.get("target_format") or "md"),
                kind=str(raw.get("kind") or "business"),  # type: ignore[arg-type]
                pages=pages,
                notes=str(raw.get("notes") or ""),
            )
        )
    return out


def page_grade_public_contract() -> dict[str, Any]:
    """Honest API surface: scale exists; business corpus has not been read."""
    return {
        "scale": "page_grade",
        "grades": dict(PAGE_GRADES),
        "checks": list(PAGE_CHECKS),
        "corpus_min": CORPUS_MIN,
        "corpus_max": CORPUS_MAX,
        "corpus_read": CORPUS_READ,
        "not_a_success_rate": True,
        "note": (
            "逐页 A/B/C/D，不折成成功率。"
            "业务语料对读完成前 corpus_read=false，不能用这把尺子对外承诺。"
        ),
    }
