"""Validace dat vizualizace před vykreslením."""

from __future__ import annotations

from ..agent.grounding import number_in_source
from ..models import VizSpec, VizType
from .safe_math import ExpressionError, sample

GRAPH_TYPES = {VizType.FLOWCHART, VizType.STATE, VizType.CAUSAL, VizType.CONCEPT}
TREE_TYPES = {VizType.TREE, VizType.MINDMAP}
TABLE_TYPES = {VizType.COMPARISON, VizType.TABLE}
CHART_TYPES = {VizType.BAR, VizType.LINE, VizType.SCATTER, VizType.HISTOGRAM}
MAX_NODES = 40
MAX_LABEL = 120


def _tree_size(node) -> int:
    return 1 + sum(_tree_size(c) for c in node.children)


def _tree_labels_ok(node) -> bool:
    return bool(node.label.strip()) and all(_tree_labels_ok(c) for c in node.children)


def validate_spec(spec: VizSpec, source_numbers: set[float] | None = None) -> tuple[list[str], list[str]]:
    """Vrátí (chyby, upozornění). Chyby znamenají, že vizualizaci nelze korektně vykreslit
    nebo by byla zavádějící. `source_numbers` = čísla nalezená ve zdrojových stránkách."""
    errors: list[str] = []
    warns: list[str] = []
    t = spec.viz_type
    if not spec.title.strip():
        errors.append("Vizualizace nemá název.")
    if not spec.explanation.strip():
        warns.append("Chybí vysvětlení, co má student z vizualizace pochopit.")

    if t in GRAPH_TYPES:
        ids = [n.id for n in spec.nodes]
        if len(spec.nodes) < 2:
            errors.append("Diagram musí mít alespoň 2 uzly.")
        if len(spec.nodes) > MAX_NODES:
            errors.append(f"Diagram má příliš mnoho uzlů ({len(spec.nodes)} > {MAX_NODES}).")
        if len(set(ids)) != len(ids):
            errors.append("Uzly mají duplicitní id.")
        if any(not n.label.strip() for n in spec.nodes):
            errors.append("Některé uzly nemají popisek.")
        bad = [f"{e.source}→{e.target}" for e in spec.edges if e.source not in ids or e.target not in ids]
        if bad:
            errors.append("Hrany odkazují na neexistující uzly: " + ", ".join(bad[:5]))
        if not spec.edges and t != VizType.CONCEPT:
            errors.append("Diagram nemá žádné vazby mezi uzly.")
        if t == VizType.CAUSAL and spec.edges and all(not e.sign for e in spec.edges):
            warns.append("U kauzálních vazeb není uveden směr působení (+/−).")
    elif t == VizType.TIMELINE:
        if len(spec.events) < 2:
            errors.append("Časová osa musí mít alespoň 2 události.")
        if any(not e.label.strip() for e in spec.events):
            errors.append("Některé události nemají popisek.")
    elif t in TREE_TYPES:
        if spec.tree is None or not spec.tree.children:
            errors.append("Strom musí mít kořen a alespoň jednu větev.")
        elif not _tree_labels_ok(spec.tree):
            errors.append("Některé uzly stromu nemají popisek.")
        elif _tree_size(spec.tree) > 60:
            errors.append("Strom je příliš velký pro přehledné zobrazení.")
    elif t in TABLE_TYPES:
        if len(spec.columns) < 2 or not spec.rows:
            errors.append("Tabulka musí mít alespoň 2 sloupce a 1 řádek.")
        elif any(len(r) != len(spec.columns) for r in spec.rows):
            errors.append("Počet buněk v řádcích neodpovídá počtu sloupců.")
    elif t == VizType.VENN:
        base = [s for s in spec.sets if "∩" not in s.label]
        if not 2 <= len(base) <= 3:
            errors.append("Vennův diagram potřebuje 2 nebo 3 množiny.")
    elif t in CHART_TYPES:
        if not spec.series:
            errors.append("Graf nemá žádná data.")
        for s in spec.series:
            if t == VizType.HISTOGRAM:
                if len(s.y) < 3:
                    errors.append("Histogram potřebuje alespoň 3 hodnoty.")
            else:
                if len(s.x) != len(s.y):
                    errors.append(f"Řada „{s.name}“ má rozdílný počet hodnot x a y.")
                if len(s.y) < 2:
                    errors.append(f"Řada „{s.name}“ má méně než 2 body.")
                if t in (VizType.LINE, VizType.SCATTER) and any(isinstance(v, str) for v in s.x):
                    if t == VizType.SCATTER:
                        errors.append("Bodový graf vyžaduje číselné hodnoty na ose x.")
        if not spec.y_label.strip():
            warns.append("Chybí popisek osy y.")
        if t != VizType.HISTOGRAM and not spec.x_label.strip():
            warns.append("Chybí popisek osy x.")
        _check_numbers(spec, source_numbers, errors, warns)
    elif t == VizType.FUNCTION:
        lo = spec.x_min if spec.x_min is not None else -10.0
        hi = spec.x_max if spec.x_max is not None else 10.0
        if not hi > lo:
            errors.append("Rozsah osy x je neplatný (x_max musí být větší než x_min).")
        else:
            try:
                _, ys = sample(spec.expression, lo, hi, 200)
                if sum(1 for y in ys if y is not None) < 20:
                    errors.append("Funkce není v zadaném rozsahu definována (příliš málo platných bodů).")
            except ExpressionError as exc:
                errors.append(str(exc))

    if t == VizType.TABLE:
        _check_numbers(spec, source_numbers, errors, warns)
    for n in spec.nodes:
        if len(n.label) > MAX_LABEL:
            warns.append(f"Popisek „{n.label[:30]}…“ je velmi dlouhý.")
    return errors, warns


def _numbers_of(spec: VizSpec) -> list[float]:
    vals: list[float] = []
    for s in spec.series:
        vals.extend(float(v) for v in s.y)
        vals.extend(float(v) for v in s.x if isinstance(v, (int, float)))
    for r in spec.rows:
        for cell in r:
            try:
                vals.append(float(cell.replace(",", ".").replace(" ", "").rstrip("%")))
            except ValueError:
                pass
    return vals


def _check_numbers(spec: VizSpec, source_numbers: set[float] | None, errors: list[str], warns: list[str]) -> None:
    if source_numbers is None:
        return
    vals = _numbers_of(spec)
    if not vals:
        return
    missing = sorted({v for v in vals if not number_in_source(v, source_numbers)})
    if not missing:
        return
    shown = ", ".join(f"{v:g}" for v in missing[:8])
    if spec.illustrative_values:
        warns.append(f"Graf obsahuje modelové hodnoty, které nejsou ve zdroji ({shown}) – je tak označen.")
    elif spec.viz_type in CHART_TYPES and not source_numbers:
        errors.append("Zdroj neobsahuje žádné číselné údaje – číselný graf by byl falešně přesný. "
                      "Zvolte diagram nebo hodnoty označte jako modelové.")
    else:
        errors.append(f"Hodnoty {shown} nebyly nalezeny ve zdrojových stránkách. Použijte jen čísla "
                      "ze zdroje, nebo je označte jako modelové (illustrative_values).")
