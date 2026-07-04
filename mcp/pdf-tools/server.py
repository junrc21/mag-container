#!/usr/bin/env python3
"""MAG PDF Tools MCP server (stdio, Python).

Exposes PDF utility tools to the Hermes agent that are unavailable via
execute_code on client channels (WhatsApp/Telegram). Only reads/writes files
within /opt/data — no arbitrary filesystem access.

Tools:
  extract_pdf_images    — extract embedded JPEG/PNG images from a cached PDF
  generate_pdf_report   — create a PDF report with title, date, images and captions
"""

import json
import os
import sys

SERVER_NAME = "mag-pdf-tools"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSION = "2025-06-18"

OUTPUT_DIR = "/opt/data/workspace"


def log(*args):
    print(f"[mag-pdf-tools] {' '.join(str(a) for a in args)}", file=sys.stderr, flush=True)


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def reply(id_, result):
    send({"jsonrpc": "2.0", "id": id_, "result": result})


def reply_error(id_, code, msg):
    send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}})


TOOLS = [
    {
        "name": "extract_pdf_images",
        "description": (
            "Extrai imagens embutidas de um PDF (fotos, figuras, gráficos) e salva como arquivos de imagem separados. "
            "Retorna os caminhos de cada imagem extraída — use MEDIA:<path> em sua resposta para enviá-las ao chat. "
            "Use quando o usuário pedir para mostrar fotos/imagens de dentro de um PDF."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pdf_path": {
                    "type": "string",
                    "description": (
                        "Caminho absoluto para o arquivo PDF. "
                        "Normalmente: /opt/data/cache/documents/doc_<hash>_<nome>.pdf"
                    ),
                },
                "max_images": {
                    "type": "integer",
                    "description": "Número máximo de imagens a extrair (padrão: 10). Use valores menores para economizar.",
                    "default": 10,
                },
                "min_size": {
                    "type": "integer",
                    "description": "Largura mínima em pixels para incluir uma imagem (padrão: 100). Filtra ícones/logos pequenos.",
                    "default": 100,
                },
            },
            "required": ["pdf_path"],
        },
    },
    {
        "name": "generate_pdf_report",
        "description": (
            "Gera um relatório em PDF com título, data e imagens com legendas. "
            "Use quando o usuário pedir para criar/gerar um documento PDF com fotos. "
            "Retorna o caminho do PDF gerado — inclua MEDIA:<path> na resposta para enviá-lo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Título do relatório.",
                },
                "images": {
                    "type": "array",
                    "description": "Lista de imagens a incluir.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Caminho absoluto da imagem."},
                            "caption": {"type": "string", "description": "Legenda da imagem."},
                        },
                        "required": ["path"],
                    },
                },
                "subtitle": {
                    "type": "string",
                    "description": "Subtítulo ou descrição opcional.",
                    "default": "",
                },
                "output_name": {
                    "type": "string",
                    "description": "Nome do arquivo de saída sem extensão (padrão: 'relatorio').",
                    "default": "relatorio",
                },
                "images_per_row": {
                    "type": "integer",
                    "description": "Imagens por linha no layout (1 ou 2, padrão: 1).",
                    "default": 1,
                },
            },
            "required": ["title", "images"],
        },
    },
]


def _safe_path(path: str) -> str:
    """Resolve and validate that path is under /opt/data."""
    real = os.path.realpath(path)
    if not real.startswith("/opt/data/"):
        raise ValueError(f"Path must be under /opt/data (got: {path})")
    return real


def extract_pdf_images(pdf_path: str, max_images: int = 10, min_size: int = 100):
    real = _safe_path(pdf_path)
    if not os.path.isfile(real):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        import pymupdf
    except ImportError:
        raise RuntimeError("pymupdf is not installed in this environment")

    img_dir = os.path.join(OUTPUT_DIR, "pdf_images")
    os.makedirs(img_dir, exist_ok=True)

    doc = pymupdf.open(real)
    extracted = []
    seen_xrefs = set()

    for page_num, page in enumerate(doc):
        if len(extracted) >= max_images:
            break
        for img_index, img in enumerate(page.get_images(full=True)):
            if len(extracted) >= max_images:
                break
            xref = img[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            base_image = doc.extract_image(xref)
            width = base_image["width"]
            height = base_image["height"]
            if width < min_size or height < min_size:
                continue

            ext = base_image["ext"]
            filename = f"page{page_num + 1}_img{img_index + 1}.{ext}"
            filepath = os.path.join(img_dir, filename)
            with open(filepath, "wb") as f:
                f.write(base_image["image"])

            extracted.append({
                "path": filepath,
                "page": page_num + 1,
                "width": width,
                "height": height,
                "ext": ext,
            })
            log(f"  Extracted: {filename} ({width}x{height})")

    doc.close()
    log(f"Total extracted: {len(extracted)} images from {real}")
    return extracted


def generate_pdf_report(title: str, images: list, subtitle: str = "",
                        output_name: str = "relatorio", images_per_row: int = 1):
    from datetime import date

    today = date.today().strftime("%d/%m/%Y")

    # Validate all image paths
    valid_images = []
    for item in images:
        p = item.get("path", "")
        caption = item.get("caption", "")
        if not p:
            continue
        try:
            real = _safe_path(p)
        except ValueError as e:
            log(f"  Skipping image {p}: {e}")
            continue
        if not os.path.isfile(real):
            log(f"  Skipping missing image: {real}")
            continue
        valid_images.append({"path": real, "caption": caption})

    if not valid_images:
        raise ValueError("No valid images found. Check that the image paths are correct and under /opt/data.")

    # Build HTML with embedded images as base64 to avoid chromium file:// security restrictions
    import base64
    import mimetypes

    def img_to_data_url(path: str) -> str:
        mime, _ = mimetypes.guess_type(path)
        if not mime:
            mime = "image/jpeg"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    col_width = "100%" if images_per_row == 1 else "48%"
    col_style = "display:inline-block;vertical-align:top;"

    img_html_parts = []
    for i, item in enumerate(valid_images):
        try:
            data_url = img_to_data_url(item["path"])
        except Exception as e:
            log(f"  Failed to encode image {item['path']}: {e}")
            continue
        caption_html = f'<p class="caption">{item["caption"]}</p>' if item["caption"] else ""
        img_html_parts.append(
            f'<div style="width:{col_width};{col_style}margin:8px 1%;">'
            f'<img src="{data_url}" style="max-width:100%;height:auto;border:1px solid #ddd;">'
            f'{caption_html}</div>'
        )

    subtitle_html = f'<p class="subtitle">{subtitle}</p>' if subtitle else ""
    images_html = "\n".join(img_html_parts)

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; margin: 30px; font-size: 13px; color: #222; }}
  h1 {{ color: #1a1a2e; border-bottom: 2px solid #6c3483; padding-bottom: 8px; margin-bottom: 4px; }}
  .subtitle {{ color: #555; margin: 4px 0 8px 0; font-size: 12px; }}
  .date {{ color: #777; font-size: 11px; margin-bottom: 20px; }}
  .caption {{ text-align: center; color: #444; font-size: 11px; margin-top: 4px; }}
  .images-grid {{ width: 100%; }}
</style>
</head>
<body>
<h1>{title}</h1>
{subtitle_html}
<p class="date">Data: {today}</p>
<div class="images-grid">
{images_html}
</div>
</body>
</html>"""

    html_path = os.path.join(OUTPUT_DIR, f"{output_name}.html")
    pdf_path = os.path.join(OUTPUT_DIR, f"{output_name}.pdf")

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML written: {html_path} ({len(html)//1024}KB)")

    # Convert to PDF via chromium
    import subprocess
    cmd = [
        "/usr/bin/chromium",
        "--headless",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--run-all-compositor-stages-before-draw",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={pdf_path}",
        f"file://{html_path}",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=60)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Chromium failed (exit {result.returncode}): {err}")

    if not os.path.isfile(pdf_path):
        raise RuntimeError(f"Chromium ran but PDF was not created at {pdf_path}")

    size_kb = os.path.getsize(pdf_path) // 1024
    log(f"PDF generated: {pdf_path} ({size_kb}KB)")
    return pdf_path


def call_tool(name: str, args: dict):
    if name == "extract_pdf_images":
        pdf_path = args.get("pdf_path", "")
        if not pdf_path:
            raise ValueError("pdf_path is required")

        max_images = int(args.get("max_images") or 10)
        min_size = int(args.get("min_size") or 100)
        images = extract_pdf_images(pdf_path, max_images=max_images, min_size=min_size)

        if not images:
            return (
                f"Nenhuma imagem embutida encontrada neste PDF "
                f"(filtro de tamanho mínimo: {min_size}px). "
                "O PDF pode ser escaneado (imagem da página inteira) ou não ter fotos embutidas."
            )

        lines = [f"Extraídas {len(images)} imagens:"]
        for img in images:
            lines.append(f"  página {img['page']}: {img['width']}x{img['height']}px → {img['path']}")
        lines.append("")
        lines.append("Para enviar cada imagem ao chat, inclua uma linha MEDIA:<path> na sua resposta:")
        for img in images:
            lines.append(f"MEDIA:{img['path']}")
        return "\n".join(lines)

    elif name == "generate_pdf_report":
        title = args.get("title", "")
        if not title:
            raise ValueError("title is required")
        images = args.get("images", [])
        if not images:
            raise ValueError("images is required and must not be empty")

        subtitle = args.get("subtitle", "")
        output_name = args.get("output_name", "relatorio") or "relatorio"
        images_per_row = int(args.get("images_per_row") or 1)

        pdf_path = generate_pdf_report(
            title=title,
            images=images,
            subtitle=subtitle,
            output_name=output_name,
            images_per_row=images_per_row,
        )
        size_kb = os.path.getsize(pdf_path) // 1024
        return (
            f"PDF gerado com sucesso: {pdf_path} ({size_kb}KB)\n\n"
            f"Para enviar o PDF ao usuário, inclua na sua resposta:\n"
            f"MEDIA:{pdf_path}"
        )

    else:
        raise ValueError(f"Unknown tool: {name}")


def handle(msg: dict):
    id_ = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}

    if method == "initialize":
        return reply(id_, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        return reply(id_, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        try:
            text = call_tool(tool_name, tool_args)
            return reply(id_, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            msg_text = str(e)
            log(f"Error in {tool_name}: {msg_text}")
            return reply(id_, {
                "content": [{"type": "text", "text": f"Erro: {msg_text}"}],
                "isError": True,
            })

    if id_ is not None:
        reply_error(id_, -32601, f"Method not found: {method}")


def main():
    log(f"Started ({SERVER_NAME} {SERVER_VERSION})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log(f"Invalid JSON: {line[:80]}")
            continue
        try:
            handle(msg)
        except Exception as e:
            log(f"Unhandled error: {e}")
            if msg.get("id") is not None:
                reply_error(msg["id"], -32603, "Internal error")


if __name__ == "__main__":
    main()
