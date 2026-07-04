#!/usr/bin/env python3
"""MAG PDF Tools MCP server (stdio, Python).

Exposes PDF utility tools to the Hermes agent that are unavailable via
execute_code on client channels (WhatsApp/Telegram). Only reads/writes files
within /opt/data — no arbitrary filesystem access.

Tools:
  extract_pdf_images  — extract embedded JPEG/PNG images from a cached PDF
"""

import json
import os
import sys

SERVER_NAME = "mag-pdf-tools"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-06-18"

ALLOWED_INPUT_DIR = "/opt/data/cache/documents"
OUTPUT_DIR = "/opt/data/workspace/pdf_images"


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
    }
]


def extract_pdf_images(pdf_path: str, max_images: int = 10, min_size: int = 100):
    # Safety: only allow files under /opt/data
    real = os.path.realpath(pdf_path)
    if not real.startswith("/opt/data/"):
        raise ValueError(f"pdf_path must be under /opt/data (got: {pdf_path})")
    if not os.path.isfile(real):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        import pymupdf
    except ImportError:
        raise RuntimeError("pymupdf is not installed in this environment")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    doc = pymupdf.open(real)
    extracted = []
    seen_xrefs = set()

    for page_num, page in enumerate(doc):
        if len(extracted) >= max_images:
            break
        image_list = page.get_images(full=True)
        for img_index, img in enumerate(image_list):
            if len(extracted) >= max_images:
                break
            xref = img[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            base_image = doc.extract_image(xref)
            width = base_image["width"]
            height = base_image["height"]

            # Skip tiny images (icons, decorations)
            if width < min_size or height < min_size:
                log(f"  Skipping small image xref={xref} ({width}x{height})")
                continue

            ext = base_image["ext"]  # e.g. "jpeg", "png"
            filename = f"page{page_num + 1}_img{img_index + 1}.{ext}"
            filepath = os.path.join(OUTPUT_DIR, filename)
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


def call_tool(name: str, args: dict):
    if name != "extract_pdf_images":
        raise ValueError(f"Unknown tool: {name}")

    pdf_path = args.get("pdf_path", "")
    if not pdf_path:
        raise ValueError("pdf_path is required")

    max_images = int(args.get("max_images") or 10)
    min_size = int(args.get("min_size") or 100)

    images = extract_pdf_images(pdf_path, max_images=max_images, min_size=min_size)

    if not images:
        return (
            "Nenhuma imagem embutida encontrada neste PDF "
            f"(filtro de tamanho mínimo: {min_size}px). "
            "O PDF pode ser escaneado (imagem da página inteira) ou não ter fotos embutidas."
        )

    lines = [f"Extraídas {len(images)} imagens:"]
    for img in images:
        lines.append(
            f"  página {img['page']}: {img['width']}x{img['height']}px → {img['path']}"
        )
    lines.append("")
    lines.append("Para enviar cada imagem ao chat, inclua uma linha MEDIA:<path> na sua resposta:")
    for img in images:
        lines.append(f"MEDIA:{img['path']}")

    return "\n".join(lines)


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
