---
name: pdf-generation
description: "Generate PDF files from text, HTML, or data using chromium or pymupdf."
version: 1.0.0
author: MAG
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [PDF, Documents, Generation, Productivity, Export]
---

# PDF Generation

Two approaches depending on the content type:

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
# Add text watermark to every page
import pymupdf
doc = pymupdf.open("/opt/data/workspace/input.pdf")
for page in doc:
    page.insert_text((100, 400), "CONFIDENCIAL", fontsize=48,
                     color=(0.8, 0, 0), rotate=45)
doc.save("/opt/data/workspace/watermarked.pdf")
```

---

## Sending the PDF to the user

After generating, attach the file — the platform adapter will send it as a document:

```python
# In a tool result or message, reference the file path:
print("/opt/data/workspace/relatorio.pdf")
```

The agent should then use `send_file` or include the path in its response so the
platform adapter picks it up and delivers it as an attachment.
