"""
Synthesis report exporters (JSON, Markdown) and evidence network graph visualizations
(Mermaid flowchart & standalone interactive HTML/SVG) (EVO-023).
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from product_value_system.evidence.models import SynthesisReport


def render_synthesis_json(report: SynthesisReport) -> str:
    """Serialize a SynthesisReport to indented JSON."""
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def render_synthesis_markdown(report: SynthesisReport) -> str:
    """Render a SynthesisReport as GitHub-Flavored Markdown with provenance and Mermaid graph."""
    lines: list[str] = [
        f"# Evidence Synthesis: {report.topic}",
        "",
        f"- **Date:** {report.date.isoformat()}",
        f"- **Claims Synthesized:** {len(report.claims)}",
        f"- **Contradictions Flagged:** {len(report.contradictions)}",
        f"- **Evidence Gaps:** {len(report.evidence_gaps)}",
        "",
        f"## Synthesized Claims ({len(report.claims)})",
        "",
    ]

    if report.claims:
        for idx, claim in enumerate(report.claims, start=1):
            lines.append(f"### {idx}. {claim.statement}")
            lines.append(
                f"- **Confidence:** {claim.overall_confidence:.2f} | "
                f"**Agreement:** {claim.agreement_score:.0%}"
            )
            lines.append(f"- **Provenance:** {claim.provenance}")
            if claim.supporting_evidence:
                lines.append("- **Supporting Evidence:**")
                for ev in claim.supporting_evidence:
                    q_score = claim.quality_scores.get(ev.id, ev.confidence)
                    sample = f", n={ev.sample_size}" if ev.sample_size is not None else ""
                    lines.append(
                        f"  - `[{ev.type.value}]` ({ev.quality.value}, score={q_score:.2f}"
                        f"{sample}): {ev.claim} — _source: `{ev.source}`_"
                    )
            if claim.contradicting_evidence:
                lines.append("- **Contradicting Evidence:**")
                for ev in claim.contradicting_evidence:
                    q_score = claim.quality_scores.get(ev.id, ev.confidence)
                    lines.append(
                        f"  - `[{ev.type.value}]` ({ev.quality.value}, score={q_score:.2f}): "
                        f"{ev.claim} — _source: `{ev.source}`_"
                    )
            lines.append("")
    else:
        lines.append("_No claims synthesized._")
        lines.append("")

    lines.extend([f"## Contradictions ({len(report.contradictions)})", ""])
    if report.contradictions:
        for c in report.contradictions:
            sev = float(c.get("severity", 0.0))
            sev_label = "HIGH" if sev >= 0.7 else ("MEDIUM" if sev >= 0.4 else "LOW")
            review_tag = " **[HUMAN REVIEW REQUIRED]**" if c.get("requires_human_review") else ""
            lines.append(
                f"- **[{sev_label} {sev:.2f}]**{review_tag} "
                f"\"{c.get('evidence1', '')}\" vs. \"{c.get('evidence2', '')}\""
            )
            lines.append(f"  - _Explanation:_ {c.get('explanation', '')}")
            if c.get("suggested_research"):
                lines.append(f"  - _Suggested Research:_ {c['suggested_research']}")
    else:
        lines.append("_No contradictions detected across sources._")

    lines.extend(["", "## Evidence Gaps", ""])
    for gap in report.evidence_gaps:
        lines.append(f"- {gap}")

    lines.extend(["", "## Recommendations", ""])
    for rec in report.recommendations:
        lines.append(f"- {rec}")

    lines.extend(
        [
            "",
            "## Evidence Network",
            "",
            "```mermaid",
            render_evidence_network_mermaid(report),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_evidence_network_mermaid(report: SynthesisReport) -> str:
    """Render claim-to-source evidence network as a Mermaid ``flowchart LR`` diagram."""
    lines = ["flowchart LR"]
    source_node_ids: dict[str, str] = {}

    def _safe_label(text: str, limit: int = 48) -> str:
        clean = re.sub(r'["\[\]\(\)\r\n]+', " ", text).strip()
        return (clean[:limit] + "...") if len(clean) > limit else clean

    for c_idx, claim in enumerate(report.claims, start=1):
        cid = f"C{c_idx}"
        c_label = _safe_label(f"{claim.statement} ({claim.overall_confidence:.0%})", 54)
        lines.append(f'    {cid}["{c_label}"]')

        for ev in claim.supporting_evidence:
            src_key = f"{ev.type.value}:{ev.source}"
            if src_key not in source_node_ids:
                sid = f"S{len(source_node_ids) + 1}"
                source_node_ids[src_key] = sid
                s_label = _safe_label(f"{ev.type.value}: {ev.source}", 44)
                lines.append(f'    {sid}["{s_label}"]')
            lines.append(f"    {source_node_ids[src_key]} -->|supports| {cid}")

        for ev in claim.contradicting_evidence:
            src_key = f"{ev.type.value}:{ev.source}"
            if src_key not in source_node_ids:
                sid = f"S{len(source_node_ids) + 1}"
                source_node_ids[src_key] = sid
                s_label = _safe_label(f"{ev.type.value}: {ev.source}", 44)
                lines.append(f'    {sid}["{s_label}"]')
            lines.append(f"    {source_node_ids[src_key]} -.->|contradicts| {cid}")

    return "\n".join(lines)


def render_evidence_network_html(report: SynthesisReport) -> str:
    """Generate a self-contained interactive HTML/SVG evidence network visualization."""
    claim_nodes: list[dict[str, Any]] = []
    source_nodes_map: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    for c_idx, claim in enumerate(report.claims):
        cid = f"claim-{c_idx}"
        claim_nodes.append(
            {
                "id": cid,
                "label": claim.statement[:70] + ("..." if len(claim.statement) > 70 else ""),
                "full": claim.statement,
                "confidence": round(claim.overall_confidence * 100, 1),
                "agreement": round(claim.agreement_score * 100, 1),
                "provenance": claim.provenance,
            }
        )
        for relation, ev_list in (
            ("supports", claim.supporting_evidence),
            ("contradicts", claim.contradicting_evidence),
        ):
            for ev in ev_list:
                skey = f"{ev.type.value}:{ev.source}"
                if skey not in source_nodes_map:
                    source_nodes_map[skey] = {
                        "id": f"src-{len(source_nodes_map)}",
                        "type": ev.type.value,
                        "quality": ev.quality.value,
                        "label": f"[{ev.type.value}] {ev.source[-42:]}",
                        "source": ev.source,
                    }
                edges.append(
                    {
                        "from": source_nodes_map[skey]["id"],
                        "to": cid,
                        "relation": relation,
                        "claim": ev.claim,
                    }
                )

    source_nodes = list(source_nodes_map.values())
    graph_json = json.dumps(
        {
            "topic": report.topic,
            "sources": source_nodes,
            "claims": claim_nodes,
            "edges": edges,
        },
        ensure_ascii=False,
    )
    safe_topic = html.escape(report.topic)

    svg_height = max(340, max(len(source_nodes), len(claim_nodes), 1) * 95 + 80)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Evidence Network: {safe_topic}</title>
  <style>
    body {{
      font-family: system-ui, -apple-system, sans-serif;
      margin: 24px; background: #0f172a; color: #e2e8f0;
    }}
    h1 {{ margin-bottom: 6px; font-size: 1.4rem; }}
    .meta {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 16px; }}
    svg {{ width: 100%; border: 1px solid #334155; border-radius: 10px; background: #1e293b; }}
    .legend {{ display: flex; gap: 18px; margin-top: 12px; font-size: 0.85rem; color: #cbd5e1; }}
    .dot {{
      display: inline-block; width: 10px; height: 10px;
      border-radius: 50%; margin-right: 6px;
    }}
  </style>
</head>
<body>
  <h1>Evidence Network: {safe_topic}</h1>
  <div class="meta">
    Sources: {len(source_nodes)} &middot; Claims: {len(claim_nodes)} &middot; Links: {len(edges)}
  </div>
  <svg id="network" viewBox="0 0 1100 {svg_height}"></svg>
  <div class="legend">
    <span><span class="dot" style="background:#38bdf8"></span>Evidence Source</span>
    <span><span class="dot" style="background:#a78bfa"></span>Synthesized Claim</span>
    <span><span class="dot" style="background:#22c55e"></span>Supports (solid)</span>
    <span><span class="dot" style="background:#ef4444"></span>Contradicts (dashed)</span>
  </div>
  <script>
    const data = {graph_json};
    const svg = document.getElementById("network");
    const ns = "http://www.w3.org/2000/svg";
    const pos = {{}};
    data.sources.forEach((s, i) => {{ pos[s.id] = {{ x: 40, y: 40 + i * 90 }}; }});
    data.claims.forEach((c, i) => {{ pos[c.id] = {{ x: 640, y: 40 + i * 105 }}; }});
    data.edges.forEach((e) => {{
      const p1 = pos[e.from], p2 = pos[e.to];
      if (!p1 || !p2) return;
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", p1.x + 340); line.setAttribute("y1", p1.y + 28);
      line.setAttribute("x2", p2.x); line.setAttribute("y2", p2.y + 32);
      line.setAttribute("stroke", e.relation === "supports" ? "#22c55e" : "#ef4444");
      line.setAttribute("stroke-width", "2.2");
      if (e.relation === "contradicts") line.setAttribute("stroke-dasharray", "6,4");
      const title = document.createElementNS(ns, "title");
      title.textContent = e.relation.toUpperCase() + ": " + e.claim;
      line.appendChild(title);
      svg.appendChild(line);
    }});
    function drawBox(x, y, w, h, fill, label, sub, tooltip) {{
      const g = document.createElementNS(ns, "g");
      const rect = document.createElementNS(ns, "rect");
      rect.setAttribute("x", x); rect.setAttribute("y", y);
      rect.setAttribute("width", w); rect.setAttribute("height", h);
      rect.setAttribute("rx", "8"); rect.setAttribute("fill", fill);
      const t1 = document.createElementNS(ns, "text");
      t1.setAttribute("x", x + 12); t1.setAttribute("y", y + 24);
      t1.setAttribute("fill", "#0f172a"); t1.setAttribute("font-size", "12");
      t1.setAttribute("font-weight", "600");
      t1.textContent = label.slice(0, 48);
      const t2 = document.createElementNS(ns, "text");
      t2.setAttribute("x", x + 12); t2.setAttribute("y", y + 44);
      t2.setAttribute("fill", "#1e293b"); t2.setAttribute("font-size", "11");
      t2.textContent = sub.slice(0, 56);
      const tip = document.createElementNS(ns, "title");
      tip.textContent = tooltip;
      g.appendChild(rect); g.appendChild(t1); g.appendChild(t2); g.appendChild(tip);
      svg.appendChild(g);
    }}
    data.sources.forEach((s) => {{
      const p = pos[s.id];
      drawBox(p.x, p.y, 340, 58, "#38bdf8", s.label, "Quality: " + s.quality, s.source);
    }});
    data.claims.forEach((c) => {{
      const p = pos[c.id];
      drawBox(p.x, p.y, 410, 66, "#a78bfa", c.label,
        "Confidence: " + c.confidence + "% | Agreement: " + c.agreement + "%",
        c.full + "\\n" + c.provenance);
    }});
  </script>
</body>
</html>
"""
