"""Export diagramu do textu Mermaid (editovatelná podoba, lze vložit např. do Markdownu)."""

from __future__ import annotations

import re

from ..models import VizSpec, VizTreeNode, VizType


def _q(text: str) -> str:
    # Mermaid: uvozovky a speciální znaky v popiscích nahradíme bezpečnými entitami
    return '"' + text.replace('"', "#quot;").replace("\n", " ") + '"'


def _id(raw: str) -> str:
    return "n_" + re.sub(r"\W", "_", raw)


def to_mermaid(spec: VizSpec) -> str | None:
    t = spec.viz_type
    if t in (VizType.FLOWCHART, VizType.CAUSAL, VizType.CONCEPT):
        lines = ["flowchart TD" if t != VizType.CONCEPT else "flowchart LR"]
        for n in spec.nodes:
            lines.append(f"    {_id(n.id)}[{_q(n.label)}]")
        for e in spec.edges:
            label = (e.sign + " " if e.sign else "") + e.label
            arrow = f"-->|{_q(label.strip())}|" if label.strip() else "-->"
            lines.append(f"    {_id(e.source)} {arrow} {_id(e.target)}")
        return "\n".join(lines)
    if t == VizType.STATE:
        lines = ["stateDiagram-v2"]
        for n in spec.nodes:
            lines.append(f"    state {_q(n.label)} as {_id(n.id)}")
        for e in spec.edges:
            lines.append(f"    {_id(e.source)} --> {_id(e.target)}" + (f" : {e.label}" if e.label else ""))
        return "\n".join(lines)
    if t == VizType.TIMELINE:
        lines = ["timeline", f"    title {spec.title}"]
        for ev in spec.events:
            lines.append(f"    {ev.when.replace(':', ' ')} : {ev.label.replace(':', ' ')}")
        return "\n".join(lines)
    if t in (VizType.TREE, VizType.MINDMAP) and spec.tree:
        lines = ["mindmap"]

        def walk(n: VizTreeNode, depth: int) -> None:
            label = re.sub(r"[()\[\]{}]", " ", n.label)
            lines.append("    " * (depth + 1) + (f"root(({label}))" if depth == 0 else label))
            for c in n.children:
                walk(c, depth + 1)
        walk(spec.tree, 0)
        return "\n".join(lines)
    return None
