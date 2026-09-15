"""Extracted PDF text must not contain raw glyph names.

docling-parse 4.7.3 (locked until Docling 2.118) wrote the glyph name where a
PDF used a ligature: "/uniFB02 uorescence" instead of "fluorescence", "GLYPH<14>"
for a degree sign. A search for "flow cytometry" then cannot find the passage, so
the corpus is silently less searchable than it looks. Fixed in docling-parse
5.4.2 (docling issue 3056).

Nothing else in the suite converts a PDF -- the chunker tests build Section
objects by hand -- so without this the whole Docling dependency is unguarded and
a lock refresh could reintroduce the bug with every test still green.

Reads through the PDF backend rather than DocumentConverter on purpose: the bug
is in the text backend, and the full pipeline additionally loads a layout model
that needs torch and a ~500 MB download, which turns a 2-second check into a
50-second one for no extra coverage of the thing that broke.

The two papers are the ones that are BOTH committed to git and actually affected:
measured against docling-parse 4.7.3, Lin_2024 and Schmidt_2022 leak one glyph
name each in their first pages, while Kage_2018 leaks none. A test over Kage
alone would pass on the broken version too.
"""

from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "data" / "documents"

# Raw glyph names as docling-parse used to emit them.
ARTEFACTS = ("/uniFB0", "GLYPH<")

AFFECTED_PAPERS = ("Lin_2024_SciReports.pdf", "Schmidt_2022_SciReports.pdf")

PAGES = 3


def _extract(pdf: Path) -> str:
    from docling.backend.docling_parse_backend import DoclingParseDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument

    backend = InputDocument(
        path_or_stream=pdf,
        format=InputFormat.PDF,
        backend=DoclingParseDocumentBackend,
        filename=pdf.name,
    )._backend
    pages = min(PAGES, backend.page_count())
    return " ".join(
        cell.text for i in range(pages) for cell in backend.load_page(i).get_text_cells()
    )


@pytest.mark.parametrize("name", AFFECTED_PAPERS)
def test_no_glyph_names_leak_into_extracted_text(name):
    pdf = DOCS / name
    if not pdf.is_file():
        pytest.skip(f"{name} not present (the example corpus may have been replaced)")

    text = _extract(pdf)

    # Positive control first: with no text at all every artefact count is zero and
    # the real assertion below would pass on a backend that extracts nothing.
    assert len(text) > 1000, f"extracted only {len(text)} chars from {name}"

    found = {a: text.count(a) for a in ARTEFACTS if a in text}
    assert not found, f"{name} leaks glyph names: {found}"
