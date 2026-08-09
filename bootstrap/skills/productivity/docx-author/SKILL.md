---
name: docx-author
description: "Generate Word (.docx) documents — headings, formatted text, lists, tables, images — using python-docx (pre-installed). Use whenever the user asks for a document, relatório, contrato, ata, or any other Word file."
version: 1.0.0
author: MAG
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [docx, word, python-docx, documents, productivity, export]
---

# Word (.docx) Generation

Produce a `.docx` file on disk using `python-docx` (already installed — no setup step needed).

**Gotcha:** the pip package is called `python-docx`, but the import is `import docx`. There is a
*different*, unrelated, abandoned package on PyPI literally named `docx` — never `pip install docx`
by itself; the one already installed in this environment is the correct one, so there is no reason
to install anything here at all.

**Just `import docx` and use it — do not hand-roll the .docx ZIP/XML structure yourself.**
`python-docx` is verified installed in this environment. Writing `document.xml`/`[Content_Types].xml`
by hand is slower and far more error-prone than the API below, and throws away every convenience
(styles, headings, tables) it gives you for free. If `import docx` genuinely fails, say so and stop —
don't silently fall back to writing the ZIP by hand.

For rich formatting with tables/images/complex layout in **HTML** instead of native Word structure,
consider whether the user actually wants a PDF (see the `pdf-generation` skill) — this skill is for
when a native, further-editable **`.docx` file** is what's needed.

## Output contract

- Write to `/opt/data/workspace/<name>.docx`. Create `/opt/data/workspace/` if it does not exist.
- See "Sending the file to the user" below for delivery — do not just describe the path in prose.

## Basic structure: headings, paragraphs, styled text

```python
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from pathlib import Path

doc = Document()

doc.add_heading("Título do Documento", level=0)   # level 0 = Title style
doc.add_heading("Seção 1", level=1)
doc.add_heading("Subseção 1.1", level=2)

# Plain paragraph
doc.add_paragraph("Texto normal introdutório do documento.")

# Mixed formatting within one paragraph — build it run by run
p = doc.add_paragraph("Este trecho é normal, ")
p.add_run("este está em negrito").bold = True
p.add_run(", e este em itálico.").italic = True

# Alignment (default is left)
centered = doc.add_paragraph("Texto centralizado")
centered.alignment = WD_ALIGN_PARAGRAPH.CENTER

Path("/opt/data/workspace").mkdir(exist_ok=True, parents=True)
doc.save("/opt/data/workspace/documento.docx")
```

## Lists

```python
doc.add_paragraph("Primeiro item", style="List Bullet")
doc.add_paragraph("Segundo item", style="List Bullet")

doc.add_paragraph("Primeiro passo", style="List Number")
doc.add_paragraph("Segundo passo", style="List Number")
```

## Tables

Use the built-in `"Table Grid"` style — it always exists in the default template and renders a plain
grid. Fancier built-in styles (`"Light Grid Accent 1"`, `"Medium Shading 1 Accent 1"`, etc.) exist
too, but an invalid style name raises `KeyError` at runtime — stick to `"Table Grid"` unless the user
specifically wants a themed look, since it is verified always present.

```python
table = doc.add_table(rows=1, cols=3)
table.style = "Table Grid"

header = table.rows[0].cells
header[0].text = "Produto"
header[1].text = "Quantidade"
header[2].text = "Preço"

for produto, quantidade, preco in linhas:
    row = table.add_row().cells
    row[0].text = produto
    row[1].text = str(quantidade)
    row[2].text = preco
```

Bold a header row by walking its runs (cell `.text =` alone does not carry formatting):

```python
from docx.shared import Pt

for cell in table.rows[0].cells:
    for paragraph in cell.paragraphs:
        for run in paragraph.runs:
            run.bold = True
            run.font.size = Pt(11)
```

## Page breaks and images

```python
doc.add_page_break()

# Local image file — same file:// style workspace path as the other MAG skills.
doc.add_picture("/opt/data/workspace/pdf_images/grafico.png", width=Inches(6))
```

(`Inches` comes from `docx.shared` — `from docx.shared import Inches`.)

## Default font for the whole document

```python
from docx.shared import Pt

style = doc.styles["Normal"]
style.font.name = "Calibri"
style.font.size = Pt(11)
```

## Page margins

```python
from docx.shared import Inches

for section in doc.sections:
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
```

## Full skeleton

```python
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from pathlib import Path

doc = Document()
doc.styles["Normal"].font.name = "Calibri"
doc.styles["Normal"].font.size = Pt(11)

doc.add_heading("Relatório Mensal", level=0)
subtitle = doc.add_paragraph("Gerado automaticamente pela MAG.")
subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

doc.add_heading("Resumo", level=1)
doc.add_paragraph("Parágrafo com o resumo executivo do período.")

doc.add_heading("Detalhamento", level=1)
table = doc.add_table(rows=1, cols=2)
table.style = "Table Grid"
table.rows[0].cells[0].text = "Item"
table.rows[0].cells[1].text = "Valor"
for item, valor in [("Receita", "R$ 12.000"), ("Custos", "R$ 4.500")]:
    row = table.add_row().cells
    row[0].text = item
    row[1].text = valor

Path("/opt/data/workspace").mkdir(exist_ok=True, parents=True)
doc.save("/opt/data/workspace/relatorio.docx")
```

## When NOT to use this skill

- The user wants a PDF, not an editable Word file — use `pdf-generation` instead.
- The user wants a spreadsheet with live formulas — use `excel-author` instead.
- Reading/extracting text from an existing `.docx` the user sent — that is a different, already
  existing capability (document reading), not this skill.

## Sending the file to the user

After saving, include a `MEDIA:` line in your reply — the platform adapter delivers it as a file attachment:

```
MEDIA:/opt/data/workspace/relatorio.docx
```

Example full response:
```
Pronto! Aqui está o documento gerado.
MEDIA:/opt/data/workspace/relatorio.docx
```
