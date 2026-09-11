#!/usr/bin/env python3
"""
generate-github-status.py
--------------------------
Busca dados reais da API publica do GitHub (perfil, repositorios, atividade)
e gera uma secao "status" no mesmo estilo de log de servidor usado no
generate-skills.py: timestamps, tags [INFO]/[ OK ]/[ERROR], categorias em
arvore (├─ / └─), mesma paleta de cores.

Gera dois arquivos:
  github-status.svg  -> versao animada para embutir no README
  github-status.txt   -> versao em texto puro (pra colar num bloco ```text)

Uso:
    python3 generate-github-status.py --user seu-usuario
    python3 generate-github-status.py --user seu-usuario --token SEU_TOKEN
    python3 generate-github-status.py --input github.json
    python3 generate-github-status.py --user seu-usuario --mock   # sem internet, dados de exemplo
"""

import argparse
import html
import json
import sys
import urllib.error
import urllib.request
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
SEPARATOR_WIDTH = 80
TS_SAMPLE = "0000-00-00 00:00:00"

API_BASE = "https://api.github.com"
LANG_TOP_N = 5
LANG_BAR_WIDTH = 20


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# ---------------------------------------------------------------------------
# Coleta de dados na API do GitHub
# ---------------------------------------------------------------------------

def _headers(token: str | None):
    return {
        "User-Agent": "github-status-script",
        "Accept": "application/vnd.github+json",
        **({"Authorization": f"token {token}"} if token else {}),
    }


def api_get(path: str, token: str | None):
    req = urllib.request.Request(f"{API_BASE}{path}", headers=_headers(token))
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_get_full(url: str, token: str | None):
    """Igual a api_get, mas recebe uma URL completa (usado para languages_url)."""
    req = urllib.request.Request(url, headers=_headers(token))
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_languages(repos, token: str | None):
    """Soma os bytes por linguagem em todos os repositorios (via languages_url)."""
    totals: dict[str, int] = {}
    for r in repos:
        url = r.get("languages_url")
        if not url:
            continue
        try:
            data = api_get_full(url, token)
        except (urllib.error.URLError, urllib.error.HTTPError):
            continue
        for lang, count in data.items():
            totals[lang] = totals.get(lang, 0) + count
    return totals


def fetch_github_data(username: str, token: str | None):
    profile = api_get(f"/users/{username}", token)

    repos, page = [], 1
    while True:
        chunk = api_get(f"/users/{username}/repos?per_page=100&page={page}&type=owner", token)
        if not chunk:
            break
        repos.extend(chunk)
        if len(chunk) < 100 or page >= 5:
            break
        page += 1

    total_stars = sum(r.get("stargazers_count", 0) for r in repos)
    total_forks = sum(r.get("forks_count", 0) for r in repos)

    try:
        events = api_get(f"/users/{username}/events/public?per_page=1", token)
        last_event = events[0] if events else None
    except urllib.error.HTTPError:
        last_event = None

    languages = fetch_languages(repos, token)

    return profile, repos, total_stars, total_forks, last_event, languages


def mock_github_data(username: str):
    """Dados de exemplo, usados com --mock (sem chamadas de rede)."""
    profile = {
        "login": username,
        "name": "The Octocat",
        "followers": 12800,
        "following": 9,
        "public_repos": 8,
        "created_at": "2011-01-25T18:44:36Z",
    }
    repos = [{"stargazers_count": 1500, "forks_count": 300}, {"stargazers_count": 900, "forks_count": 120}]
    last_event = {"type": "PushEvent", "created_at": datetime.now().isoformat() + "Z"}
    languages = {
        "Python": 52000,
        "JavaScript": 21000,
        "TypeScript": 12500,
        "HTML": 9000,
        "CSS": 5200,
        "Shell": 3100,
    }
    return (
        profile,
        repos,
        sum(r["stargazers_count"] for r in repos),
        sum(r["forks_count"] for r in repos),
        last_event,
        languages,
    )


# ---------------------------------------------------------------------------
# Monta as entradas do log (independente do formato de saida)
# ---------------------------------------------------------------------------

def build_entries(cfg: dict, username: str, token: str | None, mock: bool):
    hostname = cfg.get("hostname", "server")
    user = cfg.get("user", "root")
    command = cfg.get("command", f"./github-status --user {username}")

    base = datetime.now().replace(microsecond=0)
    entries = [
        {"kind": "prompt", "user": user, "hostname": hostname, "command": command},
        {"kind": "blank"},
        {"kind": "info", "ts": base, "msg": "Connecting to api.github.com..."},
        {"kind": "separator"},
        {"kind": "blank"},
    ]

    try:
        if mock:
            profile, repos, total_stars, total_forks, last_event, languages = mock_github_data(username)
        else:
            profile, repos, total_stars, total_forks, last_event, languages = fetch_github_data(username, token)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        ts = base + timedelta(seconds=1)
        entries.append({"kind": "error", "ts": ts, "msg": f"Failed to reach api.github.com ({exc})"})
        entries.append({"kind": "blank"})
        entries.append({"kind": "prompt_end", "user": user, "hostname": hostname})
        return entries

    member_since = profile.get("created_at", "")[:10]

    ts1 = base + timedelta(seconds=1)
    entries.append({"kind": "category", "ts": ts1, "name": "Profile"})
    entries.append(_kv("Username", profile.get("login", username)))
    entries.append(_kv("Name", profile.get("name") or "-"))
    entries.append(_kv("Followers", str(profile.get("followers", 0))))
    entries.append(_kv("Following", str(profile.get("following", 0))))
    entries.append(_kv("Member since", member_since, last=True))
    entries.append({"kind": "blank"})

    ts2 = base + timedelta(seconds=2)
    entries.append({"kind": "category", "ts": ts2, "name": "Repositories"})
    entries.append(_kv("Public repos", str(profile.get("public_repos", len(repos)))))
    entries.append(_kv("Total stars", str(total_stars)))
    entries.append(_kv("Total forks", str(total_forks), last=True))
    entries.append({"kind": "blank"})

    ts3 = base + timedelta(seconds=3)
    entries.append({"kind": "category", "ts": ts3, "name": "Languages"})
    entries.extend(build_language_items(languages))
    entries.append({"kind": "blank"})

    ts4 = base + timedelta(seconds=4)
    entries.append({"kind": "category", "ts": ts4, "name": "Activity"})
    if last_event:
        entries.append(_kv("Last event", last_event.get("type", "-")))
        entries.append(_kv("Last active", last_event.get("created_at", "-")[:10], last=True))
    else:
        entries.append(_kv("Last event", "no recent public activity", last=True))
    entries.append({"kind": "blank"})

    entries.append({"kind": "info", "ts": ts4, "msg": "Status check complete."})
    entries.append({"kind": "blank"})
    entries.append({"kind": "prompt_end", "user": user, "hostname": hostname})

    return entries


def _kv(label: str, value: str, last: bool = False):
    return {"kind": "item", "branch": "└─" if last else "├─", "label": label, "value": value}


def build_language_items(languages: dict, top_n: int = LANG_TOP_N, bar_width: int = LANG_BAR_WIDTH):
    """Gera os itens 'kv' da categoria Languages, com barra e percentual,
    no mesmo estilo dos demais blocos (├─ / └─)."""
    if not languages:
        return [_kv("Languages", "no data available", last=True)]

    total = sum(languages.values()) or 1
    top = sorted(languages.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    items = []
    for i, (lang, count) in enumerate(top):
        pct = count / total * 100
        filled = round(pct / 100 * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        items.append(_kv(lang, f"{bar}  {pct:4.1f}%", last=(i == len(top) - 1)))
    return items


def pad_kv_entries(entries):
    """Alinha os valores de cada bloco de itens (kv) baseado no maior label
    dentro daquele bloco de categoria."""
    i = 0
    while i < len(entries):
        if entries[i]["kind"] == "category":
            j = i + 1
            block = []
            while j < len(entries) and entries[j]["kind"] == "item":
                block.append(entries[j])
                j += 1
            max_label = max((len(e["label"]) for e in block), default=0)
            for e in block:
                e["padded_label"] = e["label"].ljust(max_label)
            i = j
        else:
            i += 1
    return entries


def fmt_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Renderer: texto puro
# ---------------------------------------------------------------------------

def render_plain_text(entries) -> str:
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
        elif kind == "error":
            lines.append(f'[{fmt_ts(e["ts"])}] [ERROR] {e["msg"]}')
        elif kind == "separator":
            lines.append("─" * SEPARATOR_WIDTH)
        elif kind == "category":
            lines.append(f'[{fmt_ts(e["ts"])}] [ OK ]  < {e["name"].upper()} >')
        elif kind == "item":
            label = e.get("padded_label", e["label"])
            lines.append(" " * tree_indent + f'{e["branch"]} {label}   {e["value"]}')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Renderer: SVG animado
# ---------------------------------------------------------------------------

def line_spans(entry, theme, tree_indent_chars):
    dim = theme["text_dim"]
    default = theme["text_default"]
    blue = theme["accent_blue"]
    green = theme["ok_green"]
    red = theme.get("warn_red", "#f85149")

    kind = entry["kind"]
    if kind == "prompt":
        return 0, [(entry["user"], blue), (f'@{entry["hostname"]}:~# {entry["command"]}', default)]
    if kind == "prompt_end":
        return 0, [(entry["user"], blue), (f'@{entry["hostname"]}:~# ', default), ("_", theme["cursor"])]
    if kind == "blank":
        return 0, [("", default)]
    if kind == "separator":
        return 0, [("─" * SEPARATOR_WIDTH, dim)]
    if kind == "info":
        return 0, [("[", dim), (fmt_ts(entry["ts"]), blue), ("] ", dim), ("[INFO]", dim), ("  ", default), (entry["msg"], default)]
    if kind == "error":
        return 0, [("[", dim), (fmt_ts(entry["ts"]), blue), ("] ", dim), ("[ERROR]", red), (" ", default), (entry["msg"], default)]
    if kind == "category":
        return 0, [
            ("[", dim), (fmt_ts(entry["ts"]), blue), ("] ", dim),
            ("[ OK ]", green), ("  ", default),
            ("< ", dim), (entry["name"].upper(), blue), (" >", dim),
        ]
    if kind == "item":
        label = entry.get("padded_label", entry["label"])
        return tree_indent_chars, [(f'{entry["branch"]} ', dim), (f"{label}   ", default), (entry["value"], default)]
    return 0, [("", default)]


def spans_length(indent_chars, spans):
    return indent_chars + sum(len(t) for t, _ in spans)


def render_svg(entries, theme, animate: bool = True) -> str:
    tree_indent_chars = len(f"[{TS_SAMPLE}] [ OK ]  ")
    rendered = [line_spans(e, theme, tree_indent_chars) for e in entries]
    max_len = max(spans_length(ind, spans) for ind, spans in rendered)

    width = int(PAD_X * 2 + max_len * CHAR_WIDTH)
    width = max(width, 480)
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
        f'viewBox="0 0 {width} {height}" role="img" aria-label="GitHub status log">',
        style,
        f'<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" class="term-bg term-border"/>',
    ]

    y = PAD_TOP + FONT_SIZE
    delay = 0.0
    for (indent_chars, spans), e in zip(rendered, entries):
        x = PAD_X + indent_chars * CHAR_WIDTH
        attrs = f' style="animation-delay:{delay:.2f}s"' if animate else ""
        parts.append(f'<g class="line"{attrs}>')
        parts.append(f'<text x="{x}" y="{y}">')
        for txt, color in spans:
            if txt == "":
                continue
            cls = ' class="cursor-block"' if e["kind"] == "prompt_end" and txt == "_" else ""
            parts.append(f'<tspan fill="{color}"{cls}>{esc(txt)}</tspan>')
        parts.append("</text></g>")
        y += LINE_HEIGHT
        if e["kind"] != "blank":
            delay += LINE_DELAY

    parts.append("</svg>")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Gera github-status.svg/.txt a partir da API do GitHub")
    parser.add_argument("--input", "-i", default="github.json", help="Arquivo de config (hostname/user/theme)")
    parser.add_argument("--user", help="Usuario do GitHub (sobrepoe o username do config)")
    parser.add_argument("--token", default=None, help="Token do GitHub (opcional, aumenta o limite de requisicoes)")
    parser.add_argument("--output", "-o", default="github-status.svg")
    parser.add_argument("--text-output", default="github-status.txt")
    parser.add_argument("--no-animation", action="store_true")
    parser.add_argument("--mock", action="store_true", help="Usa dados de exemplo, sem acessar a rede")
    args = parser.parse_args()

    cfg = {}
    input_path = Path(args.input)
    if input_path.exists():
        cfg = json.loads(input_path.read_text(encoding="utf-8"))

    username = args.user or cfg.get("username")
    if not username:
        print("Erro: informe --user SEU_USUARIO ou defina 'username' no github.json", file=sys.stderr)
        sys.exit(1)

    theme = cfg.get("theme", {
        "background": "#0d1117", "border": "#21262d", "text_default": "#c9d1d9",
        "text_dim": "#6e7681", "accent_blue": "#1f6feb", "ok_green": "#3fb950",
        "warn_red": "#f85149", "cursor": "#58a6ff",
    })

    entries = build_entries(cfg, username, args.token, args.mock)
    entries = pad_kv_entries(entries)

    svg = render_svg(entries, theme, animate=not args.no_animation)
    Path(args.output).write_text(svg, encoding="utf-8")
    print(f"OK: gerado {args.output} ({Path(args.output).stat().st_size} bytes)")

    txt = render_plain_text(entries)
    Path(args.text_output).write_text(txt, encoding="utf-8")
    print(f"OK: gerado {args.text_output} ({Path(args.text_output).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
