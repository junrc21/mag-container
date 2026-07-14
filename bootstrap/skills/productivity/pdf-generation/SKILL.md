---
name: pdf-generation
description: "Generate PDF files from text, HTML, or data using chromium or pymupdf."
version: 1.1.0
author: MAG
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [PDF, Documents, Generation, Productivity, Export]
---

# PDF Generation

**On client channels (WhatsApp/Telegram): this skill's raw `/usr/bin/chromium` commands require `execute_code`, which is NOT available there (the toolset is removed on client channels, not just denied — see `ocr-and-documents` skill). Use the `generate_pdf_report` tool from the `pdf-tools` MCP instead — it does the same HTML→chromium conversion server-side, accepts a `body` field for narrative text plus `images` with captions, and works without `execute_code`. For merging/splitting/watermarking an existing PDF (not generating a new one from scratch), see `ocr-and-documents`'s "Split, Merge, Watermark & Search" section — same MCP, `pdf_split`/`pdf_merge`/`pdf_watermark`.**

**On server/CLI (`execute_code` available):** two approaches depending on the content type:

| Approach | Best for | Tool |
|----------|----------|------|
| chromium HTML→PDF | Rich formatting, tables, images, charts | `/usr/bin/chromium` |
| pymupdf | Programmatic creation, merge/split existing PDFs | `pymupdf` (pre-installed) |

---

## Approach 1: HTML → PDF (via chromium)

**Preferred for documents with rich formatting.**

```bash
# Step 1: write HTML to a temp file in the workspace
cat > /opt/data/workspace/output.html << 'HTML'
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  body { font-family: Arial, sans-serif; margin: 40px; font-size: 14px; color: #222; }
  h1   { color: #1a1a2e; border-bottom: 2px solid #6c3483; padding-bottom: 8px; }
  h2   { color: #6c3483; margin-top: 24px; }
  table { width: 100%; border-collapse: collapse; margin: 16px 0; }
  th, td { border: 1px solid #ccc; padding: 8px 12px; text-align: left; }
  th   { background: #f0eaf8; font-weight: bold; }
  tr:nth-child(even) td { background: #fafafa; }
</style>
</head>
<body>
<h1>Título do Relatório</h1>
<p>Conteúdo gerado automaticamente pelo MAG.</p>
<!-- ... more content ... -->
</body>
</html>
HTML

# Step 2: convert to PDF (suppress harmless dbus errors with 2>/dev/null)
/usr/bin/chromium \
  --headless \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --run-all-compositor-stages-before-draw \
  --print-to-pdf=/opt/data/workspace/relatorio.pdf \
  "file:///opt/data/workspace/output.html" 2>/dev/null

echo "PDF saved: /opt/data/workspace/relatorio.pdf"
```

**Inline (one-liner for simple content):**
```bash
/usr/bin/chromium --headless --no-sandbox --disable-dev-shm-usage --disable-gpu \
  --print-to-pdf=/opt/data/workspace/output.pdf \
  "data:text/html,<h1>Hello</h1><p>Content here</p>" 2>/dev/null
```

**Embedding local images in the PDF:**
Reference image files directly with `file://` paths in `<img>` tags — do NOT try to `read_file` on them:
```html
<img src="file:///opt/data/workspace/pdf_images/page1_img1.jpeg" style="max-width:100%;height:auto;">
<img src="file:///opt/data/workspace/pdf_images/page2_img1.jpeg" style="max-width:100%;height:auto;">
```
Chromium resolves `file://` paths at render time. No need to read the image bytes.

**Notes:**
- Always use `--no-sandbox --disable-dev-shm-usage --disable-gpu` (required in Docker)
- Redirect stderr to `/dev/null` to hide harmless dbus/GPU errors
- Output file goes to `/opt/data/workspace/` so the agent can read/send it
- Page size defaults to US Letter; add `--virtual-time-budget=2000` if JS needs time to render

---

## Approach 2: Programmatic PDF (pymupdf)

**Best for merging, splitting, annotating existing PDFs or simple text documents.**

```python
import pymupdf

# Create a new PDF from scratch
doc = pymupdf.open()
page = doc.new_page()  # A4 by default
page.insert_text((72, 72), "Título do Documento", fontsize=18)
page.insert_text((72, 110), "Conteúdo gerado pelo MAG.", fontsize=12)
doc.save("/opt/data/workspace/output.pdf")
doc.close()
```

```python
# Merge multiple PDFs
import pymupdf
result = pymupdf.open()
for path in ["/opt/data/workspace/a.pdf", "/opt/data/workspace/b.pdf"]:
    result.insert_pdf(pymupdf.open(path))
result.save("/opt/data/workspace/merged.pdf")
```

```python
# Add text watermark to every page — insert_text's own `rotate` param only accepts
# multiples of 90 (rotate=45 raises "bad rotate value"); use `morph` (pivot point +
# rotation matrix) for a true diagonal stamp. On client channels, use the pdf-tools
# MCP's pdf_watermark tool instead (see the ocr-and-documents skill) — no execute_code needed.
import pymupdf
doc = pymupdf.open("/opt/data/workspace/input.pdf")
text, fontsize = "CONFIDENCIAL", 48
length = pymupdf.get_text_length(text, fontsize=fontsize)
for page in doc:
    center = pymupdf.Point(page.rect.width / 2, page.rect.height / 2)
    page.insert_text((page.rect.width / 2 - length / 2, page.rect.height / 2), text,
                      fontsize=fontsize, color=(0.8, 0, 0), fill_opacity=0.3,
                      morph=(center, pymupdf.Matrix(45)))
doc.save("/opt/data/workspace/watermarked.pdf")
```

---

## Sending the PDF to the user

After generating, include a `MEDIA:` line in your reply — the platform adapter delivers it as a file attachment:

```
MEDIA:/opt/data/workspace/relatorio.pdf
```

Example full response:
```
Pronto! Aqui está o relatório gerado.
MEDIA:/opt/data/workspace/relatorio.pdf
```
