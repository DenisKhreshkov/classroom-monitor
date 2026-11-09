import io
import json
from unittest.mock import Mock

import pytest
from pypdf import PdfWriter

from errors import ProviderError
from materials import MaterialReader, google_target


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://docs.google.com/document/d/abc-123/edit", ("drive", "abc-123")),
        ("https://drive.google.com/open?id=xyz", ("drive", "xyz")),
        ("https://docs.google.com/forms/d/abc/edit", ("form", "abc")),
        ("https://docs.google.com/forms/d/e/public/viewform", None),
        ("https://docs.google.com.evil.example/document/d/x", None),
        ("http://127.0.0.1/secrets", None),
        ("https://forms.gle/short", None),
    ],
)
def test_allowlisted_targets(url, expected):
    assert google_target(url) == expected


def attach(assignment):
    assignment.raw["materials"] = [
        {"driveFile": {"driveFile": {"id": "abc", "title": "Instructions"}}}
    ]
    assignment.raw["description"] = ""


def test_google_doc_export(assignment):
    attach(assignment)
    api = Mock()
    api.get.side_effect = [
        {"mimeType": "application/vnd.google-apps.document"},
        b"A graded project.",
    ]
    result = MaterialReader(api).read(assignment)
    assert result[0]["status"] == "read"
    assert result[0]["text"] == "A graded project."
    assert api.get.call_args.args[0].endswith("/export")


def test_inaccessible_is_explicit_and_not_fabricated(assignment):
    attach(assignment)
    api = Mock()
    api.get.side_effect = ProviderError("Forbidden")
    result = MaterialReader(api).read(assignment)
    assert result[0]["status"] == "unread"
    assert "text" not in result[0]


def test_disabled_and_external_links_never_fetched(assignment):
    attach(assignment)
    api = Mock()
    MaterialReader(api, enabled=False).read(assignment)
    api.get.assert_not_called()
    assignment.raw["materials"] = [{"link": {"url": "https://example.com/file.pdf"}}]
    assert MaterialReader(api).read(assignment)[0]["status"] == "unread"
    api.get.assert_not_called()


def test_forms_only_questions_no_responses():
    api = Mock()
    api.get.return_value = {
        "info": {"title": "Quiz"},
        "items": [{"title": "Question"}],
        "responses": ["PRIVATE"],
    }
    data = json.loads(MaterialReader(api).extract("form", "abc"))
    assert data["items"][0]["title"] == "Question"
    assert "responses" not in data


def test_pdf_and_oversized_file():
    api = Mock()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    api.get.side_effect = [{"mimeType": "application/pdf", "size": 1000}, buffer.getvalue()]
    assert MaterialReader(api).extract("drive", "abc") == ""
    api.get.side_effect = [{"mimeType": "application/pdf", "size": 6_000_000}]
    with pytest.raises(ProviderError, match="5 MB"):
        MaterialReader(api).extract("drive", "abc")


def test_document_text_is_bounded(assignment):
    attach(assignment)
    api = Mock()
    api.get.side_effect = [{"mimeType": "application/vnd.google-apps.document"}, b"x" * 20000]
    result = MaterialReader(api).read(assignment)
    assert len(result[0]["text"]) == 16000
    assert "truncated" in result[0]["note"]
