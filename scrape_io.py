"""
novelbuddy.io — Universal Chapter Downloader
Works for any novel on the site.

Requirements: stdlib only (no pip installs needed)
"""

import re
import os
import csv
import json
import time
import zipfile
import html as html_lib
from textwrap import wrap
from xml.sax.saxutils import escape as xml_escape
from urllib.request import urlopen, Request

BASE_URL   = "https://novelbuddy.io"
DELAY      = 0.100  # 100 milliseconds buffer time
MAX_ERRORS = 5
CHAPTER_BATCH_SIZE = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_raw(url: str) -> str:
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> dict:
    return json.loads(fetch_raw(url))


def get_build_id(novel_slug: str) -> str:
    print("  Detecting Next.js build ID ...")
    html = fetch_raw(f"{BASE_URL}/{novel_slug}")
    for pat in [r'"buildId"\s*:\s*"([^"]+)"',
                r'/_next/static/([^/]+)/_buildManifest\.js']:
        m = re.search(pat, html)
        if m:
            bid = m.group(1)
            print(f"  Build ID: {bid}")
            return bid
    raise RuntimeError("Could not detect Next.js build ID. Site may have changed.")


def fetch_props(build_id: str, novel_slug: str, ch_slug: str) -> dict | None:
    url = f"{BASE_URL}/_next/data/{build_id}/{novel_slug}/{ch_slug}.json"
    try:
        return fetch_json(url).get("pageProps", {})
    except Exception as e:
        print(f"    [WARN] {ch_slug}: {e}")
        return None


def html_to_text(raw: str) -> str:
    text = html_lib.unescape(raw)
    text = re.sub(r'<br\s*/?>', '\n',   text, flags=re.IGNORECASE)
    text = re.sub(r'</p>',      '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</div>',    '\n',   text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>',   '',     text)
    text = re.sub(r'\n{3,}',    '\n\n', text)
    return text.strip()


def safe_filename(slug: str, title: str) -> str:
    m = re.search(r'chapter-(\d+)', slug)
    num = int(m.group(1)) if m else 0
    safe = re.sub(r'[\\/:*?"<>|]', '', title)
    safe = re.sub(r'\s+', '_', safe.strip())[:60]
    return f"{num:04d}_{safe}.txt"


def safe_slug_name(slug: str) -> str:
    return re.sub(r'[^\w\-]', '_', slug)


def resume_path_for(novel_slug: str) -> str:
    return safe_slug_name(novel_slug) + "_resume.json"


def load_resume(novel_slug: str) -> dict | None:
    path = resume_path_for(novel_slug)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_resume(novel_slug: str, novel_title: str, next_slug: str):
    if not next_slug:
        return
    path = resume_path_for(novel_slug)
    data = {
        "novel_slug": novel_slug,
        "novel_title": novel_title,
        "next_slug": next_slug,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def parse_ch_num(name: str, slug: str) -> int | None:
    for pat, src in [(r'[Cc]hapter\s*(\d+)', name), (r'chapter-(\d+)', slug)]:
        m = re.search(pat, src)
        if m:
            return int(m.group(1))
    return None


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def chapter_num_from_file(filename: str) -> int | None:
    m = re.match(r'(\d+)_', filename)
    if m:
        return int(m.group(1))
    m = re.search(r'[Cc]hapter[_\s-]*(\d+)', filename)
    if m:
        return int(m.group(1))
    return None


def find_chapter_files(chapter_dir: str, start_chapter: int, end_chapter: int) -> list[tuple[int, str]]:
    files = []
    for name in os.listdir(chapter_dir):
        if not name.lower().endswith(".txt"):
            continue
        num = chapter_num_from_file(name)
        if num is not None and start_chapter <= num <= end_chapter:
            files.append((num, os.path.join(chapter_dir, name)))
    return sorted(files, key=lambda item: (item[0], item[1].lower()))


def combine_chapter_text(chapter_files: list[tuple[int, str]], title: str = "") -> str:
    chunks = []
    if title:
        chunks.append(title.strip())
        chunks.append("=" * min(max(len(title.strip()), 12), 70))

    for _, path in chapter_files:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read().strip()
        chunks.append(body)

    return "\n\n".join(chunks).strip() + "\n"


def unique_output_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 2
    while True:
        candidate = f"{root}_{i}{ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


def save_combined_txt(text: str, out_path: str) -> str:
    out_path = unique_output_path(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path


def write_index_csv(index_csv: str, rows: list[dict], fields: list[str]):
    existing = []
    if os.path.exists(index_csv):
        with open(index_csv, "r", newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))

    merged = {}
    for row in existing + rows:
        key = row.get("slug") or row.get("file") or str(row.get("chapter_num", ""))
        if key:
            merged[key] = row

    def sort_key(row):
        try:
            return int(row.get("chapter_num") or 0)
        except (TypeError, ValueError):
            return 0

    with open(index_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(merged.values(), key=sort_key))


def save_docx(text: str, out_path: str) -> str:
    out_path = unique_output_path(out_path)
    paragraphs = []
    for para in text.split("\n\n"):
        # If this paragraph is a chapter marker, render as a Heading1
        if para.startswith("CHAPTER:"):
            title = para[len("CHAPTER:"):].strip()
            paragraphs.append(
                f"<w:p><w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr>"
                f"<w:r><w:t>{xml_escape(title)}</w:t></w:r></w:p>"
            )
            continue

        lines = para.splitlines() or [""]
        run_xml = "<w:br/>".join(
            f"<w:t>{xml_escape(line)}</w:t>" if line else "<w:t></w:t>"
            for line in lines
        )
        paragraphs.append(f"<w:p><w:r>{run_xml}</w:r></w:p>")

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{''.join(paragraphs)}<w:sectPr/></w:body>
</w:document>"""

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return out_path


def pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def save_pdf(text: str, out_path: str) -> str:
    out_path = unique_output_path(out_path)
    page_lines = []
    current = []
    for raw_line in text.splitlines():
        wrapped = wrap(raw_line, width=88, replace_whitespace=False) if raw_line else [""]
        for line in wrapped:
            current.append(line)
            if len(current) >= 48:
                page_lines.append(current)
                current = []
    if current or not page_lines:
        page_lines.append(current)

    objects = []
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    page_ids = [3 + i * 2 for i in range(len(page_lines))]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>")

    for i, lines in enumerate(page_lines):
        page_id = page_ids[i]
        content_id = page_id + 1
        stream_lines = ["BT", "/F1 11 Tf", "50 780 Td", "14 TL"]
        for line_index, line in enumerate(lines):
            if line_index:
                stream_lines.append("T*")
            stream_lines.append(f"({pdf_escape(line)}) Tj")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> "
            f"/Contents {content_id} 0 R >>"
        )
        objects.append(f"<< /Length {len(stream.encode('latin-1', errors='replace'))} >>\nstream\n{stream}\nendstream")

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for obj_id, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{obj_id} 0 obj\n".encode("latin-1"))
        pdf.extend(obj.encode("latin-1", errors="replace"))
        pdf.extend(b"\nendobj\n")

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    )

    with open(out_path, "wb") as f:
        f.write(pdf)
    return out_path


def combine_and_export(chapter_dir: str = "", novel_title: str = ""):
    if not chapter_dir:
        chapter_dir = input("Chapter folder path: ").strip().strip('"')
    if not os.path.isdir(chapter_dir):
        print(f"ERROR: Folder not found: {chapter_dir}")
        return

    start_text = input("Start chapter number: ").strip()
    end_text = input("End chapter number: ").strip()
    if not start_text.isdigit() or not end_text.isdigit():
        print("ERROR: Start and end must be chapter numbers.")
        return

    start_chapter = int(start_text)
    end_chapter = int(end_text)
    if start_chapter > end_chapter:
        start_chapter, end_chapter = end_chapter, start_chapter

    chapter_files = find_chapter_files(chapter_dir, start_chapter, end_chapter)
    if not chapter_files:
        print("ERROR: No matching .txt chapter files found.")
        return

    if not novel_title:
        novel_title = input("Book title for combined file (optional): ").strip()

    default_name = f"chapters_{start_chapter:04d}_{end_chapter:04d}"
    output_name = input(f"Output base name (press Enter for '{default_name}'): ").strip() or default_name
    output_name = re.sub(r'[\\/:*?"<>|]', '', output_name)
    output_dir = chapter_dir
    text = combine_chapter_text(chapter_files, novel_title)

    created = []
    txt_path = save_combined_txt(text, os.path.join(output_dir, output_name + ".txt"))
    created.append(txt_path)

    formats = input("Also create docx/pdf? Enter docx, pdf, both, or none [both]: ").strip().lower() or "both"
    if formats in {"docx", "both", "all"}:
        created.append(save_docx(text, os.path.join(output_dir, output_name + ".docx")))
    if formats in {"pdf", "both", "all"}:
        created.append(save_pdf(text, os.path.join(output_dir, output_name + ".pdf")))

    print("\nCombined chapters:")
    print(f"  Range : {start_chapter} to {end_chapter}")
    print(f"  Count : {len(chapter_files)} files")
    for path in created:
        print(f"  Saved : {path}")


def run(novel_slug: str, novel_title: str, first_slug: str, start_save_chapter: int = 1, download_all: bool = False):
    out_dir   = safe_slug_name(novel_slug) + "_chapters"
    index_csv = safe_slug_name(novel_slug) + "_index.csv"
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 62)
    print(f"  {novel_title}")
    print(f"  Slug  : {novel_slug}")
    print(f"  Output: ./{out_dir}/")
    print(f"  Saving from chapter: {start_save_chapter}")
    print(f"  Mode  : {'all chapters' if download_all else '100-chapter batches'}")
    print("=" * 62)

    build_id = get_build_id(novel_slug)
    props = fetch_props(build_id, novel_slug, first_slug)
    if props is None:
        build_id = get_build_id(novel_slug)
        props = fetch_props(build_id, novel_slug, first_slug)
        if props is None:
            print("ERROR: Cannot fetch first chapter. Check the slug.")
            return

    print(f"\nDownloading chapters ...\n")

    index           = []
    current_slug    = first_slug
    consecutive_err = 0
    total_dl        = 0
    total_skip      = 0
    batch_count     = 0

    while current_slug:
        if props is None:
            props = fetch_props(build_id, novel_slug, current_slug)

        if props is None:
            consecutive_err += 1
            print(f"  [!] Failed ({consecutive_err}/{MAX_ERRORS}): {current_slug}")
            if consecutive_err >= MAX_ERRORS:
                print("  Too many consecutive errors — stopping.")
                break
            m = re.match(r'(chapter-)(\d+)', current_slug)
            if m:
                current_slug = f"chapter-{int(m.group(2)) + 1}"
                props = None
                continue
            break

        consecutive_err = 0

        ch         = props.get("initialChapter", {})
        next_info  = props.get("nextChapter") or {}
        ch_slug    = ch.get("slug", current_slug)
        ch_name    = ch.get("name", current_slug)
        ch_url     = BASE_URL + ch.get("url", f"/{novel_slug}/{ch_slug}")
        ch_updated = ch.get("updated_at", "")
        ch_words   = ch.get("word_count", "")
        ch_num     = parse_ch_num(ch_name, ch_slug)
        raw        = ch.get("content", "")
        out_path   = os.path.join(out_dir, safe_filename(ch_slug, ch_name))

        should_save = ch_num is None or ch_num >= start_save_chapter

        if not should_save:
            if ch_num % 25 == 0 or ch_num + 1 == start_save_chapter:
                print(f"  [skip] [{ch_num:>3}] {ch_name}")
        elif os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            total_skip += 1
            print(f"  [skip] {ch_name}")
        else:
            body = html_to_text(raw) if raw else "(No content)"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(body + "\n")
            total_dl += 1
            print(f"  [OK] [{ch_num or '?':>3}] {ch_name}  ({ch_words} words)")

        if should_save:
            index.append({
                "chapter_num": ch_num,
                "title":       ch_name,
                "slug":        ch_slug,
                "url":         ch_url,
                "updated_at":  ch_updated,
                "word_count":  ch_words,
                "file":        os.path.basename(out_path),
            })
            batch_count += 1

        next_slug = next_info.get("slug", "") if isinstance(next_info, dict) else ""
        if not next_slug or next_slug == ch_slug:
            print("\n  Chain complete — no more chapters.")
            break

        if batch_count >= CHAPTER_BATCH_SIZE:
            save_resume(novel_slug, novel_title, next_slug)
            print(f"\n  Saved {CHAPTER_BATCH_SIZE} chapters in this batch.")
            print(f"  Next batch slug: {next_slug}")
            print(f"  Resume file    : {resume_path_for(novel_slug)}")
            if not download_all and not ask_yes_no("  Download the next 100 chapters?", default=False):
                print("  Stopping at your request.")
                break
            batch_count = 0
            print()

        current_slug = next_slug
        props = None
        time.sleep(DELAY)

    fields = ["chapter_num", "title", "slug", "url", "updated_at", "word_count", "file"]
    write_index_csv(index_csv, index, fields)

    print("\n" + "=" * 62)
    print(f"  Done!  {total_dl} downloaded, {total_skip} skipped")
    print(f"  Files : ./{out_dir}/")
    print(f"  Index : {index_csv}")
    print("=" * 62)


def main():
    print("novelbuddy.io Chapter Downloader")
    print("-" * 34)
    print("1. Download chapters")
    print("2. Combine/export existing chapter txt files")
    print("3. Download chapters, then combine/export")
    choice = input("Choose option [1]: ").strip() or "1"

    if choice in {"1", "3"}:
        novel_slug  = input("Novel slug (from URL, e.g. 'under-the-oak-tree'): ").strip()
        novel_title = input("Novel title (for display): ").strip()
        resume = load_resume(novel_slug)
        if resume and resume.get("next_slug"):
            print(f"Saved next batch slug found: {resume['next_slug']}")
            if ask_yes_no("Use it to start immediately from the next batch?", default=True):
                first_slug = resume["next_slug"]
                start_save_chapter = 1
                download_all = ask_yes_no("Download all remaining chapters without asking every 100?", default=False)
                print()
                run(novel_slug, novel_title or resume.get("novel_title", ""), first_slug, start_save_chapter, download_all)
                return

        first_slug  = input("First chapter slug (press Enter for 'chapter-1'): ").strip()
        if not first_slug:
            first_slug = "chapter-1"
        start_text = input("Start saving from chapter number (press Enter for 1): ").strip()
        start_save_chapter = int(start_text) if start_text.isdigit() else 1
        download_all = ask_yes_no("Download all chapters without asking every 100?", default=False)
        print()
        run(novel_slug, novel_title, first_slug, start_save_chapter, download_all)

        if choice == "3" or ask_yes_no("\nCombine/export chapters now?", default=False):
            out_dir = safe_slug_name(novel_slug) + "_chapters"
            combine_and_export(out_dir, novel_title)
    elif choice == "2":
        combine_and_export()
    else:
        print("Unknown option. Please run again and choose 1, 2, or 3.")


if __name__ == "__main__":
    main()
