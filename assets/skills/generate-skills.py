#!/usr/bin/env python3
"""
generate-skills.py
-------------------
Le um arquivo skills.json e gera:
  1) skills.svg   -> versao animada (estilo "digitando"), para embutir no README
  2) skills.txt   -> versao em texto puro (a mesma que fica dentro do bloco
                      ```text no README), com timestamps reais no momento da geracao

Formato do log: cabecalho de boot, categorias marcadas com [ OK ], itens em
arvore (├─ / └─) e um prompt de encerramento com cursor.

Uso:
    python3 generate-skills.py
    python3 generate-skills.py --input skills.json --output skills.svg
    python3 generate-skills.py --no-animation
"""

import argparse
import html
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

FONT_FAMILY = "SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace"
FONT_SIZE = 13
LINE_HEIGHT = 20
CHAR_WIDTH = 7.85
PAD_X = 22
PAD_TOP = 18
PAD_BOTTOM = 22
LINE_DELAY = 0.09
LINE_DURATION = 0.30
SEPARATOR_WIDTH = 80  # em caracteres

TS_SAMPLE = "0000-00-00 00:00:00"  # usado só pra medir comprimento (formato fixo)


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def normalize_item(item):
    """Aceita:
    - string simples                         -> item folha
    - {"label": ..., "note": ...}             -> item folha com nota entre parênteses
    - {"group": ..., "items": [...]}          -> subcategoria (recursiva)
    """
    if isinstance(item, dict) and "group" in item:
        return {"type": "group", "name": item["group"], "items": item.get("items", [])}
    if isinstance(item, dict):
        return {"type": "leaf", "label": item.get("label", ""), "note": item.get("note")}
    return {"type": "leaf", "label": item, "note": None}


def flatten_items(items, prefix=""):
    """Percorre a lista de itens (com possíveis subgrupos aninhados) e gera
    as linhas da árvore no estilo do comando `tree`: ├─ / └─ / │  ."""
    lines = []
    nodes = [normalize_item(it) for it in items]

    leaves_with_note = [n for n in nodes if n["type"] == "leaf" and n["note"]]
    max_label = max((len(n["label"]) for n in leaves_with_note), default=0)

    for idx, node in enumerate(nodes):
        is_last = idx == len(nodes) - 1
        branch = "└─ " if is_last else "├─ "

        if node["type"] == "group":
            lines.append({"kind": "item", "prefix": prefix + branch, "label": node["name"], "note": None, "is_group": True})
            child_prefix = prefix + ("   " if is_last else "│  ")
            lines.extend(flatten_items(node["items"], child_prefix))
        else:
            note_text = None
            if node["note"]:
                pad = " " * max(2, (max_label - len(node["label"])) + 3)
                note_text = f"{pad}({node['note']})"
            lines.append({"kind": "item", "prefix": prefix + branch, "label": node["label"], "note": note_text})

    return lines


def count_leaves(items):
    total = 0
    for it in items:
        node = normalize_item(it)
        if node["type"] == "group":
            total += count_leaves(node["items"])
        else:
            total += 1
    return total


def build_entries(data: dict):
    """Monta uma lista neutra de 'entradas' de log (independente do formato
    de saida — texto puro ou SVG). Cada entrada e um dict com 'kind' + dados."""
    hostname = data.get("hostname", "server")
    user = data.get("user", "root")
    command = data.get("command", "./skills --list")

    base = datetime.now().replace(microsecond=0)
    entries = []

    entries.append({"kind": "prompt", "user": user, "hostname": hostname, "command": command})
    entries.append({"kind": "blank"})
    entries.append({"kind": "info", "ts": base, "msg": "Loading skills..."})
    entries.append({"kind": "separator"})
    entries.append({"kind": "blank"})

    total_items = 0
    last_ts = base
    for i, cat in enumerate(data["categories"]):
        ts = base + timedelta(seconds=1 + i // 2)
        last_ts = ts
        entries.append({"kind": "category", "ts": ts, "name": cat["name"]})

        entries.extend(flatten_items(cat["items"]))
        total_items += count_leaves(cat["items"])
        entries.append({"kind": "blank"})

    entries.append({"kind": "info", "ts": last_ts, "msg": f"Total skills loaded: {total_items}"})
    entries.append({"kind": "blank"})
    entries.append({"kind": "prompt_end", "user": user, "hostname": hostname})

    return entries, total_items


def fmt_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Renderer: texto puro (para o bloco ```text do README)
# ---------------------------------------------------------------------------

def render_plain_text(data: dict) -> str:
    entries, _ = build_entries(data)
    tree_indent = len(f"[{TS_SAMPLE}] [ OK ]  ")
    lines = []

    for e in entries:
        kind = e["kind"]
        if kind == "prompt":
            lines.append(f'{e["user"]}@{e["hostname"]}:~# {e["command"]}')
        elif kind == "prompt_end":
            lines.append(f'{e["user"]}@{e["hostname"]}:~# _')
        elif kind == "blank":
            lines.append("")
        elif kind == "info":
            lines.append(f'[{fmt_ts(e["ts"])}] [INFO]  {e["msg"]}')
        elif kind == "separator":
            lines.append("─" * SEPARATOR_WIDTH)
        elif kind == "category":
            lines.append(f'[{fmt_ts(e["ts"])}] [ OK ]  < {e["name"].upper()} >')
        elif kind == "item":
            note = e["note"] or ""
            lines.append(" " * tree_indent + f'{e["prefix"]}{e["label"]}{note}')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Renderer: SVG animado (com as cores solicitadas)
# ---------------------------------------------------------------------------

def line_spans(entry, theme, tree_indent_chars):
    """Retorna (indent_chars, [(texto, cor), ...]) para uma entrada."""
    dim = theme["text_dim"]
    default = theme["text_default"]
    blue = theme["accent_blue"]
    green = theme["ok_green"]

    kind = entry["kind"]
    if kind == "prompt":
        return 0, [(entry["user"], blue), (f'@{entry["hostname"]}:~# {entry["command"]}', default)], False
    if kind == "prompt_end":
        return 0, [(entry["user"], blue), (f'@{entry["hostname"]}:~# ', default), ("_", theme["cursor"])], False
    if kind == "blank":
        return 0, [("", default)], False
    if kind == "separator":
        return 0, [("─" * SEPARATOR_WIDTH, dim)], False
    if kind == "info":
        return 0, [("[", dim), (fmt_ts(entry["ts"]), blue), ("] ", dim), ("[INFO]", dim), ("  ", default), (entry["msg"], default)], False
    if kind == "category":
        return 0, [
            ("[", dim), (fmt_ts(entry["ts"]), blue), ("] ", dim),
            ("[ OK ]", green), ("  ", default),
            ("< ", dim), (entry["name"].upper(), blue), (" >", dim),
        ], False
    if kind == "item":
        spans = [(entry["prefix"], dim), (entry["label"], default)]
        if entry["note"]:
            spans.append((entry["note"], dim))
        return tree_indent_chars, spans, entry.get("is_group", False)
    return 0, [("", default)], False


def spans_length(indent_chars, spans):
    return indent_chars + sum(len(t) for t, _ in spans)


def render_svg(data: dict, animate: bool = True) -> str:
    theme = data["theme"]
    entries, total_items = build_entries(data)
    tree_indent_chars = len(f"[{TS_SAMPLE}] [ OK ]  ")

    rendered = [line_spans(e, theme, tree_indent_chars) for e in entries]
    max_len = max(spans_length(ind, spans) for ind, spans, _ in rendered)

    width = int(PAD_X * 2 + max_len * CHAR_WIDTH)
    width = max(width, 520)
    content_height = len(rendered) * LINE_HEIGHT
    height = PAD_TOP + content_height + PAD_BOTTOM

    style = f"""
    <style>
      .term-bg {{ fill: {theme['background']}; }}
      .term-border {{ fill: none; stroke: {theme['border']}; stroke-width: 1; }}
      text {{ font-family: {FONT_FAMILY}; font-size: {FONT_SIZE}px; }}
      .line {{ opacity: {0 if animate else 1}; }}
"""
    if animate:
        style += """
      @keyframes typeIn {
        from { opacity: 0; transform: translateY(2px); }
        to   { opacity: 1; transform: translateY(0); }
      }
      @keyframes blink {
        0%, 49% { opacity: 1; }
        50%, 100% { opacity: 0; }
      }
      .line { animation: typeIn """ + f"{LINE_DURATION}s ease-out forwards;" + """ }
      .cursor-block { animation: blink 1s step-end infinite; }
"""
    style += "    </style>\n"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="95%" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="Skills terminal log">',
        style,
        f'<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" class="term-bg term-border"/>',
    ]

    y = PAD_TOP + FONT_SIZE
    delay = 0.0
    for (indent_chars, spans, is_group), e in zip(rendered, entries):
        x = PAD_X + indent_chars * CHAR_WIDTH
        attrs = f' style="animation-delay:{delay:.2f}s"' if animate else ""
        parts.append(f'<g class="line"{attrs}>')
        parts.append(f'<text x="{x}" y="{y}">')
        for txt, color in spans:
            if txt == "":
                continue
            cls = ' class="cursor-block"' if e["kind"] == "prompt_end" and txt == "_" else ""
            weight = ' font-weight="bold"' if is_group and color == theme["text_default"] else ""
            parts.append(f'<tspan fill="{color}"{cls}{weight}>{esc(txt)}</tspan>')
        parts.append("</text></g>")
        y += LINE_HEIGHT
        if e["kind"] != "blank":
            delay += LINE_DELAY

    parts.append("</svg>")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Gera skills.svg (e opcionalmente skills.txt) a partir de skills.json")
    parser.add_argument("--input", "-i", default="skills.json")
    parser.add_argument("--output", "-o", default="skills.svg")
    parser.add_argument("--text-output", default="skills.txt", help="Arquivo de saida do texto puro")
    parser.add_argument("--no-animation", action="store_true")
    parser.add_argument("--no-text", action="store_true", help="Nao gerar o skills.txt")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Erro: arquivo de entrada nao encontrado: {input_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(input_path.read_text(encoding="utf-8"))

    svg = render_svg(data, animate=not args.no_animation)
    Path(args.output).write_text(svg, encoding="utf-8")
    print(f"OK: gerado {args.output} ({Path(args.output).stat().st_size} bytes)")

    if not args.no_text:
        txt = render_plain_text(data)
        Path(args.text_output).write_text(txt, encoding="utf-8")
        print(f"OK: gerado {args.text_output} ({Path(args.text_output).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
