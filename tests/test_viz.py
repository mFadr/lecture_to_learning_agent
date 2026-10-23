import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lecture_to_learning.models import SourceRef, Visualization, VizSpec, VizType
from lecture_to_learning.viz.render import render_to_files, svg_to_png
from lecture_to_learning.viz.safe_math import ExpressionError, compile_expression
from lecture_to_learning.viz.svg import render_svg
from lecture_to_learning.viz.validate import validate_spec

SVG_SPECS = {
    "flowchart": {"nodes": [{"id": "a", "label": "Start <script>alert(1)</script>"}, {"id": "b", "label": "Krok"},
                            {"id": "c", "label": "Konec"}],
                  "edges": [{"source": "a", "target": "b", "label": "ano & dál"}, {"source": "b", "target": "c"},
                            {"source": "c", "target": "a", "label": "cyklus"}]},
    "causal": {"nodes": [{"id": "a", "label": "Cena"}, {"id": "b", "label": "Poptávka"}],
               "edges": [{"source": "a", "target": "b", "sign": "-"}]},
    "state": {"nodes": [{"id": "a", "label": "Klid"}, {"id": "b", "label": "Pohyb"}],
              "edges": [{"source": "a", "target": "b", "label": "síla"}, {"source": "b", "target": "a", "label": "tření"}]},
    "concept": {"nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}, {"id": "c", "label": "C"}],
                "edges": [{"source": "a", "target": "b", "label": "je typem"}]},
    "timeline": {"events": [{"when": "1776", "label": "Smith: Bohatství národů"}, {"when": "1936", "label": "Keynes"}]},
    "tree": {"tree": {"label": "Trhy", "children": [{"label": "Dokonalá konkurence"},
                                                     {"label": "Nedokonalá", "children": [{"label": "Monopol"}]}]}},
    "mindmap": {"tree": {"label": "Elasticita", "children": [{"label": "Cenová", "children": [{"label": "Elastická"}]},
                                                             {"label": "Důchodová"}]}},
    "comparison": {"columns": ["Vlastnost", "A", "B"], "rows": [["x", "1", "2"], ["y", "3", "4"]]},
    "table": {"columns": ["Cena", "Množství"], "rows": [["30", "120"], ["40", "90"]]},
    "venn": {"sets": [{"label": "Mikro", "items": ["firma"]}, {"label": "Makro", "items": ["HDP"]},
                      {"label": "Mikro ∩ Makro", "items": ["trh"]}]},
}


@pytest.mark.parametrize("vt", list(SVG_SPECS))
def test_svg_generation_is_wellformed_and_escaped(vt):
    spec = VizSpec.model_validate({"viz_type": vt, "title": f"Test {vt} <b>", "explanation": "e", **SVG_SPECS[vt]})
    errors, _ = validate_spec(spec, None)
    assert not errors
    svg = render_svg(spec, footer="Zdroj: test.pdf, s. 2")
    root = ET.fromstring(svg)  # musí být validní XML
    assert root.tag.endswith("svg")
    assert "<script" not in svg and "&lt;script&gt;" in svg or vt != "flowchart"
    assert "Zdroj: test.pdf, s. 2" in svg


def test_svg_converts_to_png(tmp_path: Path):
    spec = VizSpec.model_validate({"viz_type": "flowchart", "title": "T", **SVG_SPECS["flowchart"]})
    out = tmp_path / "a.png"
    assert svg_to_png(render_svg(spec), out)
    assert out.read_bytes()[:4] == b"\x89PNG"


def test_chart_from_verified_data(tmp_path: Path):
    spec = VizSpec.model_validate({"viz_type": "bar", "title": "Prodej", "explanation": "e",
                                   "series": [{"x": ["30 Kč", "40 Kč"], "y": [120, 90]}],
                                   "x_label": "cena", "y_label": "ks"})
    assert validate_spec(spec, {30.0, 40.0, 120.0, 90.0}) == ([], [])
    viz = Visualization(spec=spec, source=SourceRef(doc_id="d", pages=[1]))
    render_to_files(viz, tmp_path, "Zdroj: x.pdf, s. 1")
    assert (tmp_path / viz.svg_path).exists() and (tmp_path / viz.png_path).exists()
    assert "Zdroj" in (tmp_path / viz.svg_path).read_text(encoding="utf-8")


def test_chart_validation_rules():
    base = {"viz_type": "line", "title": "T", "explanation": "e", "x_label": "x", "y_label": "y"}
    # nesoulad délek
    errs, _ = validate_spec(VizSpec.model_validate({**base, "series": [{"x": [1, 2, 3], "y": [1, 2]}]}), set())
    assert any("rozdílný počet" in e for e in errs)
    # kvalitativní zdroj bez čísel → falešně přesný graf odmítnut
    errs, _ = validate_spec(VizSpec.model_validate({**base, "series": [{"x": [1, 2], "y": [5, 6]}]}), set())
    assert any("falešně přesný" in e for e in errs)
    # modelové hodnoty jsou povoleny jen s označením
    errs, warns = validate_spec(VizSpec.model_validate({**base, "illustrative_values": True,
                                                        "series": [{"x": [1, 2], "y": [5, 6]}]}), set())
    assert not errs and any("modelové" in w for w in warns)
    # neexistující uzel
    errs, _ = validate_spec(VizSpec.model_validate({"viz_type": "flowchart", "title": "T",
                                                    "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                                                    "edges": [{"source": "a", "target": "z"}]}), None)
    assert any("neexistující" in e for e in errs)


def test_function_plot_and_safe_math(tmp_path: Path):
    f = compile_expression("y = 2*x^2 - 3")
    assert f(2) == 5
    for bad in ("__import__('os').system('dir')", "x.__class__", "open('a')", "[x for x in ()]", "lambda: 1"):
        with pytest.raises(ExpressionError):
            compile_expression(bad)
    spec = VizSpec.model_validate({"viz_type": "function", "title": "Parabola", "expression": "2*x**2 - 3",
                                   "x_min": -3, "x_max": 3, "x_label": "x", "y_label": "y"})
    assert validate_spec(spec, None)[0] == []
    viz = Visualization(spec=spec, source=SourceRef(doc_id="d", pages=[1]))
    render_to_files(viz, tmp_path, "")
    assert (tmp_path / viz.png_path).exists()
    bad_range = spec.model_copy(update={"x_min": 5, "x_max": 1})
    assert validate_spec(bad_range, None)[0]
