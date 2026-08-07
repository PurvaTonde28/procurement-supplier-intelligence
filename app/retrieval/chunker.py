import fitz  # PyMuPDF

MIN_CHUNK_CHARS = 200  # merge short pages/sections up to this length before splitting further
MAX_CHUNK_CHARS = 900  # split anything longer than this


def chunk_pdf_by_page(file_path: str) -> list[dict]:
    """Returns [{'page_number': int, 'chunk_text': str}, ...].
    Page-level chunking keeps citations meaningful for short contract docs."""
    doc = fitz.open(file_path)
    chunks = []

    for page_num, page in enumerate(doc, start=1):
        raw_text = page.get_text().strip()
        if not raw_text:
            continue

        if len(raw_text) <= MAX_CHUNK_CHARS:
            chunks.append({"page_number": page_num, "chunk_text": raw_text})
        else:
            # split long pages on paragraph breaks, merge small fragments
            paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
            buffer = ""
            for para in paragraphs:
                if len(buffer) + len(para) < MAX_CHUNK_CHARS:
                    buffer += ("\n\n" if buffer else "") + para
                else:
                    if buffer:
                        chunks.append({"page_number": page_num, "chunk_text": buffer})
                    buffer = para
            if buffer:
                chunks.append({"page_number": page_num, "chunk_text": buffer})

    doc.close()
    return chunks