---
name: ocr-and-documents
description: "Extract text and images from PDFs/scans (pymupdf, tesseract OCR). Includes embedded image extraction and PDF reconstruction with images."
version: 2.4.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [PDF, Documents, Research, Arxiv, Text-Extraction, OCR, Images]
    related_skills: [powerpoint, pdf-generation]
---

# PDF & Document Extraction

For DOCX: use `python-docx` (parses actual document structure, far better than OCR).
For PPTX: see the `powerpoint` skill (uses `python-pptx` with full slide/notes support).
This skill covers **PDFs and scanned documents**.

## Step 1: Remote URL Available?

If the document has a URL, **always try `web_extract` first**:

```
web_extract(urls=["https://arxiv.org/pdf/2402.03300"])
web_extract(urls=["https://example.com/report.pdf"])
```

This handles PDF-to-markdown conversion via Firecrawl with no local dependencies.

Only use local extraction when: the file is local, web_extract fails, or you need batch processing.

## Step 2: Choose Local Extractor

| Feature | pymupdf (~25MB) | marker-pdf (~3-5GB) |
|---------|-----------------|---------------------|
| **Text-based PDF** | ✅ | ✅ |
| **Scanned PDF (OCR)** | ✅ (via tesseract) | ✅ (90+ languages) |
| **Embedded images extraction** | ✅ (see section below) | ✅ (with context) |
| **Tables** | ✅ (basic) | ✅ (high accuracy) |
| **Equations / LaTeX** | ❌ | ✅ |
| **Code blocks** | ❌ | ✅ |
| **Markdown output** | ✅ (via pymupdf4llm) | ✅ (native, higher quality) |
| **Install size** | ~25MB | ~3-5GB (PyTorch + models) |
| **Speed** | Instant | ~1-14s/page (CPU) |

**Decision**: Use pymupdf for most cases. Use marker-pdf only for equations, forms, or complex layout analysis.

---

## pymupdf — Text Extraction

```python
import pymupdf

doc = pymupdf.open("/path/to/document.pdf")
for i, page in enumerate(doc):
    text = page.get_text()
    print(f"=== Page {i+1} ===")
    print(text)
```

**Markdown output (preserves structure better):**
```python
import pymupdf4llm

md = pymupdf4llm.to_markdown("/path/to/document.pdf")
print(md)
```

---

## pymupdf — OCR for Scanned PDFs (tesseract installed)

Use when `page.get_text()` returns empty or very little text — the PDF is image-only/scanned.

```python
import pymupdf

doc = pymupdf.open("/path/to/scanned.pdf")
for i, page in enumerate(doc):
    # Try normal text first
    text = page.get_text().strip()
    if not text:
        # Page is scanned — use tesseract OCR
        # lang="por+eng" for Portuguese + English
        tp = page.get_textpage_ocr(flags=0, language="por+eng", dpi=300, full=True)
        text = page.get_text(textpage=tp)
    print(f"=== Page {i+1} ===")
    print(text)
```

---

## pymupdf — Extracting Embedded Images from PDF

**Critical for PDFs with photos** (inspection reports, catalogues, presentations with images).
This is the correct approach when a PDF has embedded photos/graphics that need to be preserved.

```python
import pymupdf
import os

doc = pymupdf.open("/path/to/document.pdf")
output_dir = "/opt/data/workspace/pdf_images"
os.makedirs(output_dir, exist_ok=True)

extracted = []  # list of {page, index, path, width, height}

for page_num, page in enumerate(doc):
    image_list = page.get_images(full=True)  # list of images on this page
    for img_index, img in enumerate(image_list):
        xref = img[0]  # image reference number
        base_image = doc.extract_image(xref)
        img_bytes = base_image["image"]
        ext = base_image["ext"]  # e.g. "jpeg", "png"
        width = base_image["width"]
        height = base_image["height"]

        filename = f"page{page_num+1}_img{img_index+1}.{ext}"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "wb") as f:
            f.write(img_bytes)

        extracted.append({
            "page": page_num + 1,
            "index": img_index + 1,
            "path": filepath,
            "width": width,
            "height": height,
            "ext": ext,
        })
        print(f"  Extracted: page {page_num+1}, image {img_index+1} → {filepath} ({width}x{height})")

print(f"\nTotal: {len(extracted)} images extracted to {output_dir}")
```

**After extracting**, send each image file path to the conversation so they appear inline — the platform will display them. Example:
```python
for img in extracted:
    print(img["path"])  # platform shows the image inline
```

---

## PDF with Embedded Images → New PDF (preserving photos)

When the user wants to **recreate or translate a PDF that contains photos**, extract the images first and then embed them in the new document via chromium HTML.

```python
import pymupdf
import pymupdf4llm
import os

src = "/path/to/original.pdf"
output_dir = "/opt/data/workspace/pdf_images"
os.makedirs(output_dir, exist_ok=True)

# Step 1: extract text as markdown
md_text = pymupdf4llm.to_markdown(src)

# Step 2: extract embedded images
doc = pymupdf.open(src)
image_refs = {}  # page_num → list of local file paths

for page_num, page in enumerate(doc):
    image_refs[page_num] = []
    for img_index, img in enumerate(page.get_images(full=True)):
        xref = img[0]
        base_image = doc.extract_image(xref)
        ext = base_image["ext"]
        path = os.path.join(output_dir, f"p{page_num+1}_i{img_index+1}.{ext}")
        with open(path, "wb") as f:
            f.write(base_image["image"])
        image_refs[page_num].append(path)

# Step 3: build HTML combining text + images, then generate PDF via chromium
# (see pdf-generation skill for the chromium command)
# Images are referenced as file:// paths in the HTML <img src="..."> tags
```

---

## Arxiv Papers

```
# Abstract only (fast)
web_extract(urls=["https://arxiv.org/abs/2402.03300"])

# Full paper
web_extract(urls=["https://arxiv.org/pdf/2402.03300"])
```

## Split, Merge & Search

```python
# Split: extract pages 1-5 to a new PDF
import pymupdf
doc = pymupdf.open("report.pdf")
new = pymupdf.open()
for i in range(5):
    new.insert_pdf(doc, from_page=i, to_page=i)
new.save("/opt/data/workspace/pages_1-5.pdf")
```

```python
# Merge multiple PDFs
import pymupdf
result = pymupdf.open()
for path in ["a.pdf", "b.pdf", "c.pdf"]:
    result.insert_pdf(pymupdf.open(path))
result.save("/opt/data/workspace/merged.pdf")
```

```python
# Search for text across all pages
import pymupdf
doc = pymupdf.open("report.pdf")
for i, page in enumerate(doc):
    if page.search_for("revenue"):
        print(f"Page {i+1}: found")
        print(page.get_text())
```

---

## Notes

- `web_extract` is always first choice for URLs
- pymupdf is the safe default — instant, no models, works everywhere
- **If `page.get_text()` returns empty → the PDF is scanned → use `get_textpage_ocr()`**
- **If the PDF has photos/images → use `page.get_images()` + `doc.extract_image()` to get them**
- marker-pdf is for complex layouts, equations — install only when needed (~3-5GB)
- For Word docs: `pip install python-docx`
- For PowerPoint: see the `powerpoint` skill
