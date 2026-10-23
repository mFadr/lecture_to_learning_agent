"""Generování SVG diagramů z validovaných dat pomocí pevných šablon.

Veškerý text je escapován; do SVG se nikdy nevkládá surový obsah od modelu ani z PDF."""

from __future__ import annotations

import math
from html import escape
from textwrap import wrap as _wrap

from ..models import VizEdge, VizNode, VizSpec, VizTreeNode, VizType

FONT = "Segoe UI, Arial, Helvetica, sans-serif"
INK = "#1f2933"
MUTED = "#52606d"
LINE = "#7b8794"
PALETTE = ["#dbeafe", "#dcfce7", "#fef3c7", "#fce7f3", "#ede9fe", "#ccfbf1", "#ffedd5", "#e0e7ff"]
STROKES = ["#2563eb", "#16a34a", "#d97706", "#db2777", "#7c3aed", "#0d9488", "#ea580c", "#4f46e5"]
POS, NEG = "#15803d", "#b91c1c"
CHAR_W = 0.56  # průměrná šířka znaku vůči velikosti písma


def esc(s: str) -> str:
    return escape(str(s), quote=True)


def wrap(text: str, width: int, max_lines: int = 5) -> list[str]:
    lines = _wrap(str(text), width=max(4, width), break_long_words=True) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(1, width - 1)] + "…"
    return lines


def text_lines(x: float, y: float, lines: list[str], size: int = 13, anchor: str = "middle",
               weight: str = "normal", fill: str = INK, line_h: float | None = None) -> str:
    # Každý řádek jako samostatný <text>: víceřádkové <tspan dy> nepodporují všechny renderery (např. QtSvg).
    lh = line_h or size * 1.25
    return "".join(
        f'<text x="{x:.1f}" y="{y + i * lh:.1f}" font-family="{FONT}" font-size="{size}" text-anchor="{anchor}" '
        f'font-weight="{weight}" fill="{fill}">{esc(ln)}</text>' for i, ln in enumerate(lines))


def document(width: float, height: float, body: str, title: str, footer: str = "", subtitle: str = "") -> str:
    head_h = 56 if subtitle else 44
    foot_h = 30 if footer else 10
    W, H = max(width, 560), height + head_h + foot_h
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}">',
        f"<title>{esc(title)}</title>",
        "<defs>"
        f'<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker>'
        f'<marker id="arrowpos" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{POS}"/></marker>'
        f'<marker id="arrowneg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{NEG}"/></marker>'
        "</defs>",
        f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" fill="#ffffff"/>',
        text_lines(16, 28, wrap(title, int((W - 32) / (17 * CHAR_W)), 1), size=17, anchor="start", weight="600"),
    ]
    if subtitle:
        parts.append(text_lines(16, 46, wrap(subtitle, int((W - 32) / (12 * CHAR_W)), 1), size=12,
                                anchor="start", fill=MUTED))
    parts.append(f'<g transform="translate(0,{head_h})">{body}</g>')
    if footer:
        parts.append(text_lines(16, H - 10, wrap(footer, int((W - 32) / (11 * CHAR_W)), 1), size=11,
                                anchor="start", fill=MUTED))
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Graf uzlů a hran (vývojový diagram, stavy, kauzální a konceptuální diagram)
# ---------------------------------------------------------------------------


def _layers(nodes: list[VizNode], edges: list[VizEdge]) -> tuple[dict[str, int], set[tuple[str, str]]]:
    ids = [n.id for n in nodes]
    out: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        if e.source in out and e.target in out and e.source != e.target:
            out[e.source].append(e.target)
    # odstranění cyklů: hrany vedoucí zpět v DFS pořadí se pro vrstvení ignorují
    back: set[tuple[str, str]] = set()
    state: dict[str, int] = {}

    def dfs(u: str) -> None:
        state[u] = 1
        for v in out[u]:
            if state.get(v) == 1:
                back.add((u, v))
            elif v not in state:
                dfs(v)
        state[u] = 2

    indeg = {i: 0 for i in ids}
    for u in ids:
        for v in out[u]:
            indeg[v] += 1
    for start in [i for i in ids if indeg[i] == 0] + ids:
        if start not in state:
            dfs(start)
    layer = {i: 0 for i in ids}
    order = _topo(ids, out, back)
    for u in order:
        for v in out[u]:
            if (u, v) not in back:
                layer[v] = max(layer[v], layer[u] + 1)
    return layer, back


def _topo(ids: list[str], out: dict[str, list[str]], back: set[tuple[str, str]]) -> list[str]:
    indeg = {i: 0 for i in ids}
    for u in ids:
        for v in out[u]:
            if (u, v) not in back:
                indeg[v] += 1
    queue = [i for i in ids if indeg[i] == 0]
    res: list[str] = []
    while queue:
        u = queue.pop(0)
        res.append(u)
        for v in out[u]:
            if (u, v) in back:
                continue
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    res += [i for i in ids if i not in res]
    return res


def render_graph(spec: VizSpec, footer: str = "") -> str:
    nodes = spec.nodes
    ids = {n.id for n in nodes}
    edges = [e for e in spec.edges if e.source in ids and e.target in ids]
    layer, back = _layers(nodes, edges)
    n_layers = max(layer.values(), default=0) + 1
    by_layer: list[list[VizNode]] = [[] for _ in range(n_layers)]
    for n in nodes:
        by_layer[layer[n.id]].append(n)
    # pořadí ve vrstvě podle průměrné pozice předchůdců (zmenšuje křížení)
    pos: dict[str, float] = {}
    for li, ln in enumerate(by_layer):
        if li > 0:
            def bary(n: VizNode) -> float:
                preds = [pos[e.source] for e in edges if e.target == n.id and e.source in pos]
                return sum(preds) / len(preds) if preds else 1e9
            ln.sort(key=bary)
        for i, n in enumerate(ln):
            pos[n.id] = i

    box_w, chars = 180, 24
    sizes = {n.id: wrap(n.label, chars, 4) for n in nodes}
    box_h = {k: 18 + 16 * len(v) for k, v in sizes.items()}
    gap_x, gap_y = 40, 70
    max_in_layer = max(len(ln) for ln in by_layer)
    width = max_in_layer * (box_w + gap_x) + gap_x
    layer_h = [max((box_h[n.id] for n in ln), default=40) for ln in by_layer]
    xy: dict[str, tuple[float, float]] = {}
    y = 20.0
    groups = sorted({n.group for n in nodes if n.group})
    for li, ln in enumerate(by_layer):
        row_w = len(ln) * (box_w + gap_x) - gap_x
        x0 = (width - row_w) / 2
        for i, n in enumerate(ln):
            xy[n.id] = (x0 + i * (box_w + gap_x), y + (layer_h[li] - box_h[n.id]) / 2)
        y += layer_h[li] + gap_y
    height = y - gap_y + 20

    parts: list[str] = []
    for e in edges:
        sx, sy = xy[e.source]
        tx, ty = xy[e.target]
        color, marker = LINE, "arrow"
        if spec.viz_type == VizType.CAUSAL and e.sign:
            color, marker = (POS, "arrowpos") if e.sign == "+" else (NEG, "arrowneg")
        sh, th = box_h[e.source], box_h[e.target]
        if e.source == e.target:
            continue
        if (e.source, e.target) in back or layer[e.target] <= layer[e.source]:
            # zpětná / vodorovná hrana – oblouk po straně
            x1, y1 = sx + box_w, sy + sh / 2
            x2, y2 = tx + box_w, ty + th / 2
            bend = max(x1, x2) + 50
            d = f"M{x1:.1f},{y1:.1f} C{bend:.1f},{y1:.1f} {bend:.1f},{y2:.1f} {x2 + 2:.1f},{y2:.1f}"
            lx, ly = bend - 10, (y1 + y2) / 2
            width = max(width, bend + 40)
        else:
            x1, y1 = sx + box_w / 2, sy + sh
            x2, y2 = tx + box_w / 2, ty - 2
            my = (y1 + y2) / 2
            d = f"M{x1:.1f},{y1:.1f} C{x1:.1f},{my:.1f} {x2:.1f},{my:.1f} {x2:.1f},{y2:.1f}"
            lx, ly = (x1 + x2) / 2, my
        dash = ' stroke-dasharray="5,4"' if spec.viz_type == VizType.CONCEPT and not e.label else ""
        parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6"{dash} marker-end="url(#{marker})"/>')
        label = e.label
        if spec.viz_type == VizType.CAUSAL and e.sign:
            label = ("+ " if e.sign == "+" else "− ") + label
        if label.strip():
            lines = wrap(label, 22, 2)
            lw = max(len(ln) for ln in lines) * 11 * CHAR_W + 10
            parts.append(f'<rect x="{lx - lw / 2:.1f}" y="{ly - 9:.1f}" width="{lw:.1f}" height="{14 * len(lines) + 4:.1f}" '
                         f'rx="3" fill="#ffffff" fill-opacity="0.92"/>')
            parts.append(text_lines(lx, ly + 3, lines, size=11, fill=color if color != LINE else MUTED, line_h=14))
    first = by_layer[0][0].id if by_layer and by_layer[0] else None
    last_layer_ids = {n.id for n in by_layer[-1]} if by_layer else set()
    for n in nodes:
        x, y0 = xy[n.id]
        h = box_h[n.id]
        gi = groups.index(n.group) if n.group in groups else (layer[n.id] if spec.viz_type == VizType.CONCEPT else 0)
        fill, stroke = PALETTE[gi % len(PALETTE)], STROKES[gi % len(STROKES)]
        rx = 8
        if spec.viz_type == VizType.STATE:
            rx = 22
        elif spec.viz_type == VizType.FLOWCHART and (n.id == first or n.id in last_layer_ids):
            rx = h / 2
        elif spec.viz_type == VizType.CONCEPT:
            rx = 16
        parts.append(f'<rect x="{x:.1f}" y="{y0:.1f}" width="{box_w}" height="{h:.1f}" rx="{rx:.1f}" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
        lines = sizes[n.id]
        parts.append(text_lines(x + box_w / 2, y0 + 9 + 13, lines, size=13, line_h=16))
    legend = ""
    if spec.viz_type == VizType.CAUSAL:
        legend = (text_lines(16, height + 8, ["Zelená šipka (+): růst příčiny zvyšuje následek;  červená (−): snižuje."],
                             size=11, anchor="start", fill=MUTED))
        height += 22
    return document(width + 20, height, "".join(parts) + legend, spec.title, footer, spec.viz_type.label)


# ---------------------------------------------------------------------------
# Časová osa
# ---------------------------------------------------------------------------


def render_timeline(spec: VizSpec, footer: str = "") -> str:
    parts: list[str] = []
    y = 24.0
    axis_x = 170
    width = 720
    rows = []
    for ev in spec.events:
        when = wrap(ev.when, 20, 2)
        label = wrap(ev.label, 60, 3)
        h = max(len(when), len(label)) * 17 + 18
        rows.append((when, label, h))
    total = sum(h for *_, h in rows)
    parts.append(f'<line x1="{axis_x}" y1="{y - 10}" x2="{axis_x}" y2="{y + total}" stroke="{LINE}" stroke-width="3"/>')
    for i, (when, label, h) in enumerate(rows):
        c = STROKES[i % len(STROKES)]
        parts.append(f'<circle cx="{axis_x}" cy="{y + 8}" r="8" fill="{c}"/>')
        parts.append(text_lines(axis_x - 18, y + 13, when, size=13, anchor="end", weight="600", fill=c, line_h=17))
        parts.append(text_lines(axis_x + 20, y + 13, label, size=13, anchor="start", line_h=17))
        y += h
    return document(width, y + 10, "".join(parts), spec.title, footer, spec.viz_type.label)


# ---------------------------------------------------------------------------
# Strom a myšlenková mapa
# ---------------------------------------------------------------------------


def _leaves(n: VizTreeNode) -> int:
    return max(1, sum(_leaves(c) for c in n.children))


def render_tree(spec: VizSpec, footer: str = "") -> str:
    assert spec.tree is not None
    if spec.viz_type == VizType.MINDMAP:
        return render_mindmap(spec.tree, spec.title, footer, spec.viz_type.label)
    col_w, box_w, row_h = 230, 190, 52
    parts: list[str] = []
    depth_max = 0

    def place(n: VizTreeNode, depth: int, top: float) -> float:
        """Vrátí y středu uzlu; strom roste zleva doprava."""
        nonlocal depth_max
        depth_max = max(depth_max, depth)
        if n.children:
            ys = []
            cur = top
            for c in n.children:
                ys.append(place(c, depth + 1, cur))
                cur += _leaves(c) * row_h
            cy = (ys[0] + ys[-1]) / 2
            for c, chy in zip(n.children, ys):
                x1, x2 = 20 + depth * col_w + box_w, 20 + (depth + 1) * col_w
                parts.insert(0, f'<path d="M{x1},{cy:.1f} C{x1 + 25},{cy:.1f} {x2 - 25},{chy:.1f} {x2},{chy:.1f}" '
                                f'fill="none" stroke="{LINE}" stroke-width="1.5"/>')
        else:
            cy = top + row_h / 2
        lines = wrap(n.label, 26, 2)
        h = 14 + 15 * len(lines)
        ci = min(depth, len(PALETTE) - 1)
        x = 20 + depth * col_w
        parts.append(f'<rect x="{x}" y="{cy - h / 2:.1f}" width="{box_w}" height="{h}" rx="7" fill="{PALETTE[ci]}" '
                     f'stroke="{STROKES[ci]}" stroke-width="1.4"/>')
        parts.append(text_lines(x + box_w / 2, cy - h / 2 + 19, lines, size=12 if depth else 13,
                                weight="600" if depth == 0 else "normal", line_h=15))
        return cy

    place(spec.tree, 0, 10)
    height = _leaves(spec.tree) * row_h + 20
    width = (depth_max + 1) * col_w + 20
    return document(width, height, "".join(parts), spec.title, footer, spec.viz_type.label)


def render_mindmap(root: VizTreeNode, title: str, footer: str = "", subtitle: str = "") -> str:
    parts_lines: list[str] = []
    parts_nodes: list[str] = []
    total = _leaves(root)
    r1, r2 = 210.0, 390.0
    cx = cy = r2 + 120
    positions: list[tuple[float, float, VizTreeNode, int, int]] = []

    def place(n: VizTreeNode, depth: int, a0: float, a1: float, branch: int, px: float, py: float) -> None:
        if depth == 0:
            x, y = cx, cy
        else:
            ang = (a0 + a1) / 2
            r = r1 if depth == 1 else r2 + (depth - 2) * 120
            x, y = cx + r * math.cos(ang), cy + r * math.sin(ang)
            parts_lines.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{x:.1f}" y2="{y:.1f}" '
                               f'stroke="{STROKES[branch % len(STROKES)]}" stroke-width="{3 if depth == 1 else 1.5}" '
                               f'stroke-opacity="0.7"/>')
        positions.append((x, y, n, depth, branch))
        a = a0
        for i, c in enumerate(n.children):
            span = (a1 - a0) * _leaves(c) / max(1, sum(_leaves(k) for k in n.children))
            place(c, depth + 1, a, a + span, i if depth == 0 else branch, x, y)
            a += span

    place(root, 0, -math.pi / 2, 3 * math.pi / 2, 0, cx, cy)
    for x, y, n, depth, branch in positions:
        width_chars = 18 if depth else 20
        lines = wrap(n.label, width_chars, 3)
        w = max(len(ln) for ln in lines) * (13 if depth < 2 else 12) * CHAR_W + 18
        h = 12 + 15 * len(lines)
        fill = "#1e3a8a" if depth == 0 else PALETTE[branch % len(PALETTE)]
        stroke = "#1e3a8a" if depth == 0 else STROKES[branch % len(STROKES)]
        color = "#ffffff" if depth == 0 else INK
        parts_nodes.append(f'<rect x="{x - w / 2:.1f}" y="{y - h / 2:.1f}" width="{w:.1f}" height="{h}" rx="{h / 2 if depth else 10:.1f}" '
                           f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
        parts_nodes.append(text_lines(x, y - h / 2 + 18, lines, size=13 if depth < 2 else 12,
                                      weight="600" if depth < 2 else "normal", fill=color, line_h=15))
    _ = total
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    minx, maxx, miny, maxy = min(xs) - 130, max(xs) + 130, min(ys) - 50, max(ys) + 50
    body = f'<g transform="translate({-minx:.1f},{-miny:.1f})">{"".join(parts_lines)}{"".join(parts_nodes)}</g>'
    return document(maxx - minx, maxy - miny, body, title, footer, subtitle)


# ---------------------------------------------------------------------------
# Tabulka / srovnávací matice
# ---------------------------------------------------------------------------


def render_table(spec: VizSpec, footer: str = "") -> str:
    cols = spec.columns
    n = len(cols)
    col_w = max(130, min(240, 900 // max(1, n)))
    chars = int(col_w / (12.5 * CHAR_W)) - 1
    parts: list[str] = []
    y = 10.0
    all_rows = [cols] + spec.rows
    for ri, row in enumerate(all_rows):
        cells = [wrap(c, chars, 6) for c in row]
        h = max(len(c) for c in cells) * 16 + 14
        for ci, lines in enumerate(cells):
            x = 10 + ci * col_w
            header = ri == 0
            first_col = ci == 0 and spec.viz_type == VizType.COMPARISON
            fill = "#1e3a8a" if header else ("#eef2ff" if first_col else ("#f8fafc" if ri % 2 else "#ffffff"))
            parts.append(f'<rect x="{x}" y="{y:.1f}" width="{col_w}" height="{h}" fill="{fill}" stroke="#cbd2d9"/>')
            parts.append(text_lines(x + 8, y + 19, lines, size=12.5, anchor="start",
                                    weight="600" if header or first_col else "normal",
                                    fill="#ffffff" if header else INK, line_h=16))
        y += h
    return document(n * col_w + 20, y + 10, "".join(parts), spec.title, footer, spec.viz_type.label)


# ---------------------------------------------------------------------------
# Vennův diagram
# ---------------------------------------------------------------------------


def render_venn(spec: VizSpec, footer: str = "") -> str:
    base = [s for s in spec.sets if "∩" not in s.label][:3]
    shared = [i for s in spec.sets if "∩" in s.label for i in s.items]
    r = 150
    if len(base) == 2:
        centers = [(220, 200), (400, 200)]
        label_pos = [(150, 40), (470, 40)]
        item_pos = [(160, 170), (460, 170)]
        mid = (310, 170)
        W, H = 640, 400
    else:
        centers = [(250, 210), (410, 210), (330, 345)]
        label_pos = [(150, 50), (510, 50), (330, 520)]
        item_pos = [(195, 175), (465, 175), (330, 410)]
        mid = (330, 245)
        W, H = 680, 540
    parts = []
    for i, ((x, y), s) in enumerate(zip(centers, base)):
        parts.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{STROKES[i]}" fill-opacity="0.16" '
                     f'stroke="{STROKES[i]}" stroke-width="2"/>')
    for i, ((lx, ly), s) in enumerate(zip(label_pos, base)):
        parts.append(text_lines(lx, ly, wrap(s.label, 24, 2), size=15, weight="600", fill=STROKES[i]))
    for (ix, iy), s in zip(item_pos, base):
        parts.append(text_lines(ix, iy, [ln for it in s.items[:6] for ln in wrap(it, 16, 2)][:8], size=12, line_h=15))
    if shared:
        parts.append(text_lines(mid[0], mid[1], [ln for it in shared[:5] for ln in wrap(it, 14, 2)][:7],
                                size=12, weight="600", line_h=15))
    return document(W, H, "".join(parts), spec.title, footer, spec.viz_type.label)


def render_svg(spec: VizSpec, footer: str = "") -> str:
    t = spec.viz_type
    if t in (VizType.FLOWCHART, VizType.STATE, VizType.CAUSAL, VizType.CONCEPT):
        return render_graph(spec, footer)
    if t == VizType.TIMELINE:
        return render_timeline(spec, footer)
    if t in (VizType.TREE, VizType.MINDMAP):
        return render_tree(spec, footer)
    if t in (VizType.COMPARISON, VizType.TABLE):
        return render_table(spec, footer)
    if t == VizType.VENN:
        return render_venn(spec, footer)
    raise ValueError(f"Typ {t} se nevykresluje jako SVG diagram.")
