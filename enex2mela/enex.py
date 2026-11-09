"""Streaming parser for Evernote .enex exports."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup
from lxml import etree

BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "hr", "table", "ul", "ol",
}


@dataclass
class Resource:
    hash: str
    mime: str
    data: bytes
    filename: str | None = None

    @property
    def is_image(self) -> bool:
        return self.mime.startswith("image/")

    @property
    def is_pdf(self) -> bool:
        return self.mime == "application/pdf"


@dataclass
class Note:
    title: str
    notebook: str
    html: str
    created: datetime | None = None
    tags: list[str] = field(default_factory=list)
    source_url: str | None = None
    resources: list[Resource] = field(default_factory=list)

    @property
    def images(self) -> list[Resource]:
        return [r for r in self.resources if r.is_image]

    @property
    def pdfs(self) -> list[Resource]:
        return [r for r in self.resources if r.is_pdf]

    @property
    def has_encrypted(self) -> bool:
        return "<en-crypt" in self.html

    def lines(self) -> list[str]:
        """Body text split into lines, one per block element / list item."""
        return html_to_lines(self.html)

    def text(self) -> str:
        return "\n".join(self.lines())

    def media_order(self) -> list[Resource]:
        """Resources in the order they appear in the body, then any unreferenced ones."""
        by_hash = {r.hash: r for r in self.resources}
        ordered: list[Resource] = []
        soup = parse_body(self.html)
        for m in soup.find_all("en-media"):
            r = by_hash.get((m.get("hash") or "").lower())
            if r and r not in ordered:
                ordered.append(r)
        ordered += [r for r in self.resources if r not in ordered]
        return ordered


XML_PROLOG = re.compile(r"<\?xml[^>]*\?>|<!DOCTYPE[^>]*>", re.I)


def parse_body(html: str) -> BeautifulSoup:
    return BeautifulSoup(XML_PROLOG.sub("", html), "html.parser")


def html_to_lines(html: str) -> list[str]:
    soup = parse_body(html)
    for tag in soup.find_all(["en-media", "en-crypt", "script", "style"]):
        tag.decompose()
    for tag in soup.find_all(["en-todo"]):
        tag.replace_with("")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    for td in soup.find_all(["td", "th"]):
        td.insert_after(" ")
    text = soup.get_text()
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return [line for line in lines if line]


def _text(el, path: str) -> str | None:
    found = el.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_resource(el) -> Resource | None:
    data_el = el.find("data")
    if data_el is None or not data_el.text:
        return None
    data = base64.b64decode("".join(data_el.text.split()))
    mime = (_text(el, "mime") or "application/octet-stream").lower()
    filename = _text(el, "resource-attributes/file-name")
    return Resource(hash=hashlib.md5(data).hexdigest(), mime=mime, data=data, filename=filename)


def iter_notes(path: str | Path) -> Iterator[Note]:
    """Yield notes one at a time so large exports with photos don't load all at once."""
    path = Path(path)
    notebook = path.stem
    context = etree.iterparse(
        str(path), events=("end",), tag="note",
        huge_tree=True, resolve_entities=False, no_network=True, load_dtd=False,
    )
    for _, el in context:
        resources = [r for r in (_parse_resource(x) for x in el.findall("resource")) if r]
        yield Note(
            title=_text(el, "title") or "Untitled",
            notebook=notebook,
            html=_text(el, "content") or "",
            created=_parse_date(_text(el, "created")),
            tags=[t.text.strip() for t in el.findall("tag") if t.text and t.text.strip()],
            source_url=_text(el, "note-attributes/source-url"),
            resources=resources,
        )
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
