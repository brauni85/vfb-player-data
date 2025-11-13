#!/usr/bin/env python3
"""Extracts VfB Stuttgart squad data from a saved HTML page."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


@dataclass
class SectionRow:
    """Single table row within a squad section."""

    values: List[str]
    link: Optional[str] = None


@dataclass
class Section:
    """Represents a squad section (e.g. Tor, Abwehr)."""

    name: str
    rows: List[SectionRow] = field(default_factory=list)


class SquadHTMLParser(HTMLParser):
    """Parse the simplified VfB squad HTML structure into sections."""

    def __init__(self) -> None:
        super().__init__()
        self.sections: List[Section] = []
        self._pending_section_name: str | None = None
        self._current_section: Section | None = None
        self._in_heading_left = False
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_row: List[str] = []
        self._current_cell_data: List[str] = []
        self._current_row_link: str | None = None

    # HTMLParser hooks -------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        attrs_dict = dict(attrs)

        if tag == "div" and "class" in attrs_dict:
            classes = attrs_dict["class"].split()
            if "heading-left" in classes:
                self._in_heading_left = True
                return

        if tag == "table" and self._pending_section_name:
            self._current_section = Section(self._pending_section_name)
            self.sections.append(self._current_section)
            self._pending_section_name = None
            self._in_table = True
            return

        if self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []
            self._current_row_link = None
            return

        if self._in_row and tag == "td":
            self._in_cell = True
            self._current_cell_data = []
            return

        if self._in_cell and tag == "img":
            title = attrs_dict.get("title") or attrs_dict.get("alt")
            if title:
                self._current_cell_data.append(title.strip())

        if self._in_cell and tag == "a":
            href = attrs_dict.get("href")
            if href and not href.lower().startswith("javascript:"):
                if self._current_row_link is None:
                    self._current_row_link = href

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag == "div" and self._in_heading_left:
            self._in_heading_left = False
            return

        if tag == "table" and self._in_table:
            self._in_table = False
            self._current_section = None
            return

        if tag == "tr" and self._in_row:
            self._in_row = False
            if self._current_section and self._current_row:
                self._current_section.rows.append(
                    SectionRow(values=self._current_row, link=self._current_row_link)
                )
            self._current_row = []
            self._current_row_link = None
            return

        if tag == "td" and self._in_cell:
            self._in_cell = False
            cell_text = "".join(self._current_cell_data).strip()
            self._current_row.append(cell_text)
            self._current_cell_data = []
            return

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._in_heading_left:
            text = data.strip()
            if text:
                self._pending_section_name = text
            return

        if self._in_cell:
            stripped = data.strip()
            if stripped:
                self._current_cell_data.append(stripped)


# Helper functions -----------------------------------------------------
def parse_sections(html: str) -> List[Section]:
    parser = SquadHTMLParser()
    parser.feed(html)
    return parser.sections


def _href_to_filename(href: str) -> str:
    """Turn a VfB profile href into a deterministic filename."""

    parsed = urlparse(href)
    path = parsed.path.rstrip("/")
    parts = [part for part in path.split("/") if part]
    if not parts:
        raise ValueError(f"Cannot derive filename from href: {href}")
    return "_".join(parts)


def download_profile_pages(
    sections: List[Section],
    base_url: str,
    output_dir: Path,
    *,
    force: bool = False,
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
) -> None:
    """Download linked profile pages for later offline parsing."""

    output_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    for section in sections:
        for row in section.rows:
            href = row.link
            if not href or href in seen:
                continue
            seen.add(href)

            absolute_url = urljoin(base_url, href)
            filename = _href_to_filename(href) + ".html"
            destination = output_dir / filename

            if destination.exists() and not force:
                print(f"Skipping existing profile: {destination}")
                continue

            request = Request(absolute_url, headers={"User-Agent": user_agent})
            try:
                with urlopen(request, timeout=30) as response:
                    content = response.read()
            except HTTPError as exc:
                print(f"Failed to download {absolute_url}: HTTP {exc.code}")
                continue
            except URLError as exc:
                print(f"Failed to download {absolute_url}: {exc.reason}")
                continue

            destination.write_bytes(content)
            print(f"Downloaded {absolute_url} -> {destination}")


def write_csv(sections: List[Section], output_path: Path) -> None:
    header = [
        "Kategorie",
        "Nr.",
        "Name",
        "Geburtsdatum",
        "Nationalität",
        "Größe",
        "Gewicht",
        "Beim VfB seit",
    ]

    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)

        for section in sections:
            for row in section.rows:
                values = row.values
                if len(values) == 7:
                    nr, name, geburtsdatum, nation, height, weight, since = values
                elif len(values) == 6:
                    nr = ""
                    name, geburtsdatum, nation, height, weight, since = values
                else:
                    raise ValueError(
                        f"Unsupported number of columns ({len(values)}) in section '{section.name}'."
                    )

                writer.writerow(
                    [
                        section.name,
                        nr,
                        name,
                        geburtsdatum,
                        nation,
                        height,
                        weight,
                        since,
                    ]
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/vfb_listenansicht_2025_2026.html"),
        help="Path to the saved VfB squad HTML page.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/vfb_kader_2025_2026.csv"),
        help="Where to write the generated CSV file.",
    )
    parser.add_argument(
        "--base-url",
        default="https://www.vfb.de",
        help="Base URL used to resolve relative profile links.",
    )
    parser.add_argument(
        "--profiles-dir",
        type=Path,
        default=Path("data/raw/profile_pages"),
        help="Directory where downloaded profile pages should be stored.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip downloading individual profile pages.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Redownload profile pages even if they already exist locally.",
    )
    parser.add_argument(
        "--user-agent",
        default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        help="User-Agent header to use for HTTP requests.",
    )

    args = parser.parse_args()

    html = args.input.read_text(encoding="utf-8")
    sections = parse_sections(html)

    if not sections:
        raise SystemExit("No squad sections could be parsed from the HTML file.")

    if not args.skip_download:
        download_profile_pages(
            sections,
            args.base_url,
            args.profiles_dir,
            force=args.force_download,
            user_agent=args.user_agent,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_csv(sections, args.output)
    print(f"Wrote {sum(len(s.rows) for s in sections)} entries to {args.output}")


if __name__ == "__main__":
    main()
