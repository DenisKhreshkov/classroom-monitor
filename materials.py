"""Only authorized Google APIs are fetched; arbitrary links are never requested."""

import io
import json
import re
from urllib.parse import parse_qs, urlparse

from pypdf import PdfReader

from errors import ProviderError

MAX_TEXT = 16_000
MAX_TOTAL_TEXT = 48_000


def google_target(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "docs.google.com",
        "drive.google.com",
        "forms.google.com",
    }:
        return None
    # A published /d/e/ respondent ID is not a Forms API formId.
    if "/forms/d/e/" in parsed.path:
        return None
    match = re.search(
        r"/(document|spreadsheets|presentation|forms|file)/d/([A-Za-z0-9_-]+)", parsed.path
    )
    if match:
        return ("form" if match[1] == "forms" else "drive", match[2])
    file_id = parse_qs(parsed.query).get("id", [None])[0]
    if (
        parsed.hostname == "drive.google.com"
        and file_id
        and re.fullmatch(r"[A-Za-z0-9_-]+", file_id)
    ):
        return "drive", file_id
    return None


class MaterialReader:
    def __init__(self, api, enabled: bool = True):
        self.api = api
        self.enabled = enabled

    def read(self, assignment) -> list[dict]:
        targets = []
        for material in assignment.raw.get("materials", []):
            drive = material.get("driveFile", {}).get("driveFile", {})
            if drive.get("id"):
                targets.append(("drive", drive["id"], drive.get("title", "Drive attachment")))
            for key, urlkey in [("link", "url"), ("form", "formUrl"), ("form", "responseUrl")]:
                url = material.get(key, {}).get(urlkey)
                if url:
                    target = google_target(url)
                    targets.append(
                        (*target, material.get(key, {}).get("title", key))
                        if target
                        else ("unread", "", material.get(key, {}).get("title", key))
                    )
            if "youtubeVideo" in material:
                targets.append(("unread", "", "YouTube video: transcript not read"))
        for url in re.findall(r"https://[^\s<>\)\]\"']+", assignment.raw.get("description", "")):
            target = google_target(url)
            targets.append(
                (*target, "Linked Google material")
                if target
                else ("unread", "", "External or published link: not read")
            )
        results, seen, total = [], set(), 0
        for kind, item_id, title in targets:
            key = (kind, item_id, title if kind == "unread" else "")
            if key in seen:
                continue
            seen.add(key)
            if len(results) >= 10:
                results.append(
                    {"status": "unread", "note": "Additional materials omitted (10-file limit)."}
                )
                break
            record = {
                "title": title[:500],
                "status": "unread",
                "note": "Content not accessible through an enabled authorized API.",
            }
            if self.enabled and kind != "unread":
                try:
                    text = self.extract(kind, item_id)
                    remaining = max(0, MAX_TOTAL_TEXT - total)
                    text_limit = min(MAX_TEXT, remaining)
                    record = {
                        "title": title[:500],
                        "status": "read" if text else "unread",
                        "text": text[:text_limit],
                        "note": "Text truncated." if len(text) > text_limit else "",
                    }
                    if not text:
                        record["note"] = (
                            "No extractable text (image/scanned PDF or unsupported file)."
                        )
                    total += len(record["text"])
                except ProviderError:
                    pass
                except Exception:
                    record["note"] = "Could not extract this material; content was not read."
            results.append(record)
        return results

    def extract(self, kind: str, item_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", item_id):
            raise ProviderError("Invalid Google material identifier.")
        if kind == "form":
            data = self.api.get(f"https://forms.googleapis.com/v1/forms/{item_id}")
            # Questions and quiz settings only, never student responses.
            return json.dumps(
                {k: data.get(k) for k in ["info", "settings", "items"]}, ensure_ascii=False
            )
        base = f"https://www.googleapis.com/drive/v3/files/{item_id}"
        meta = self.api.get(
            base, params={"fields": "id,mimeType,size", "supportsAllDrives": "true"}
        )
        mime = meta.get("mimeType", "")
        if mime == "application/vnd.google-apps.form":
            return self.extract("form", item_id)
        export_types = {
            "application/vnd.google-apps.document": "text/plain",
            "application/vnd.google-apps.presentation": "text/plain",
            "application/vnd.google-apps.spreadsheet": "text/csv",
        }
        if mime in export_types:
            data = self.api.get(
                base + "/export", params={"mimeType": export_types[mime]}, binary=True
            )
            return data.decode("utf-8", errors="replace")
        if int(meta.get("size", 0)) > 5_000_000:
            raise ProviderError("Attachment is larger than 5 MB.")
        if mime != "application/pdf" and not mime.startswith("text/"):
            return ""
        data = self.api.get(base, params={"alt": "media", "supportsAllDrives": "true"}, binary=True)
        if mime == "application/pdf":
            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages[:20]) + (
                "\n[Only the first 20 PDF pages were read.]" if len(reader.pages) > 20 else ""
            )
        return data.decode("utf-8", errors="replace")
