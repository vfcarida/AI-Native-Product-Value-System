"""
Synthesis report exporters (JSON, Markdown, Notion-compatible) and trend dashboard (EVO-022).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from product_value_system.discovery.insight_extractor import (
    InterviewInsights,
    PainPointSeverity,
)
from product_value_system.discovery.theme_clusterer import ClusteredTheme


def export_insights_json(insights: InterviewInsights) -> str:
    """Serialize interview insights as formatted JSON."""
    return json.dumps(insights.to_dict(), indent=2, ensure_ascii=False)


def export_insights_markdown(insights: InterviewInsights) -> str:
    """Render interview insights as standard GitHub-Flavored Markdown."""
    lines: list[str] = [
        f"# Interview Synthesis: {insights.interview_id}",
        "",
        f"- **Created:** {insights.created_at}",
        f"- **Review Status:** `{insights.review_status.value}`",
        f"- **Themes:** {', '.join(insights.themes) if insights.themes else 'None'}",
        f"- **PII Redacted:** {sum(insights.redaction_counts.values())} item(s)",
        "",
        "## Executive Summary",
        "",
        insights.summary or "_No summary generated._",
        "",
        f"## Pain Points ({len(insights.pain_points)})",
        "",
    ]

    if insights.pain_points:
        for idx, pp in enumerate(insights.pain_points, start=1):
            lines.append(
                f"{idx}. **[{pp.severity.value.upper()}]** {pp.description} "
                f"_(confidence: {pp.confidence:.0%}, mentions: {pp.frequency})_"
            )
            for quote in pp.quotes:
                lines.append(f'   > "{quote}"')
    else:
        lines.append("_No pain points detected._")

    lines.extend(["", f"## Jobs-to-be-Done ({len(insights.jobs_to_be_done)})", ""])
    if insights.jobs_to_be_done:
        for idx, job in enumerate(insights.jobs_to_be_done, start=1):
            lines.append(f"### {idx}. {job.job}")
            lines.append(f"- **Situation:** {job.situation}")
            lines.append(f"- **Motivation:** {job.motivation}")
            lines.append(f"- **Expected Outcome:** {job.expected_outcome}")
            lines.append(f"- **Current Solution:** {job.current_solution}")
            lines.append("")
    else:
        lines.append("_No jobs-to-be-done extracted._")
        lines.append("")

    lines.extend([f"## Key Quotes ({len(insights.quotes)})", ""])
    if insights.quotes:
        for q in insights.quotes:
            loc = f"turn {q.turn_number}" if q.turn_number is not None else "transcript"
            lines.append(
                f'- "{q.text}" — **{q.speaker}** (`{q.timestamp}`, {loc}; _context: {q.context}_)'
            )
    else:
        lines.append("_No quotes extracted._")

    lines.append("")
    return "\n".join(lines)


def export_insights_notion(insights: InterviewInsights) -> str:
    """Render interview insights in Notion-import-compatible Markdown with properties & callouts."""
    severity_badges = {
        PainPointSeverity.CRITICAL: "🔴 Critical",
        PainPointSeverity.HIGH: "🟠 High",
        PainPointSeverity.MEDIUM: "🟡 Medium",
        PainPointSeverity.LOW: "🟢 Low",
    }
    themes_tags = ", ".join(f"`{t}`" for t in insights.themes) if insights.themes else "`Unlabeled`"

    lines: list[str] = [
        f"# 🎙️ Customer Interview — {insights.interview_id}",
        "",
        "| Property | Value |",
        "| :--- | :--- |",
        f"| Interview ID | `{insights.interview_id}` |",
        f"| Date | {insights.created_at[:10]} |",
        f"| Review Status | `{insights.review_status.value}` |",
        f"| Themes | {themes_tags} |",
        f"| Pain Points | {len(insights.pain_points)} |",
        f"| JTBD Count | {len(insights.jobs_to_be_done)} |",
        f"| PII Items Redacted | {sum(insights.redaction_counts.values())} |",
        "",
        "> 💡 **Executive Summary**",
        f"> {insights.summary}",
        "",
        "---",
        "",
        "## 🎯 Pain Points",
        "",
        "| Severity | Pain Point | Confidence | Evidence Quote |",
        "| :--- | :--- | :---: | :--- |",
    ]

    for pp in insights.pain_points:
        badge = severity_badges[pp.severity]
        quote = (pp.quotes[0] if pp.quotes else "").replace("|", "\\|")
        desc = pp.description.replace("|", "\\|")
        lines.append(f'| {badge} | {desc} | {pp.confidence:.0%} | "{quote}" |')

    lines.extend(
        [
            "",
            "## 💼 Jobs-to-be-Done",
            "",
            "| Job | Situation | Motivation | Expected Outcome | Current Solution |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for jtbd in insights.jobs_to_be_done:
        row = [
            jtbd.job.replace("|", "\\|"),
            jtbd.situation.replace("|", "\\|"),
            jtbd.motivation.replace("|", "\\|"),
            jtbd.expected_outcome.replace("|", "\\|"),
            jtbd.current_solution.replace("|", "\\|"),
        ]
        lines.append(f"| {' | '.join(row)} |")

    lines.extend(["", "## 💬 Traceable Quotes", ""])
    for q in insights.quotes:
        turn_label = f"Turn #{q.turn_number}" if q.turn_number is not None else "Transcript"
        lines.append(f'> "{q.text}"')
        lines.append(
            f"> — *{q.speaker}* · `{q.interview_id}` @ `{q.timestamp}` ({turn_label}) · {q.context}"
        )
        lines.append("")

    return "\n".join(lines)


def export_insights(insights: InterviewInsights, fmt: str = "json") -> str:
    """Export insights in ``json``, ``markdown``, or ``notion`` format."""
    normalized = fmt.strip().lower()
    if normalized == "json":
        return export_insights_json(insights)
    if normalized in {"markdown", "md"}:
        return export_insights_markdown(insights)
    if normalized == "notion":
        return export_insights_notion(insights)
    raise ValueError(f"Unsupported export format: {fmt!r}. Choose json, markdown, or notion.")


def export_clustered_themes(
    themes: list[ClusteredTheme],
    *,
    total_interviews: int,
    fmt: str = "markdown",
) -> str:
    """Export clustered cross-interview themes in JSON, Markdown, or Notion format."""
    normalized = fmt.strip().lower()
    if normalized == "json":
        return json.dumps(
            {
                "total_interviews": total_interviews,
                "cluster_count": len(themes),
                "themes": [t.to_dict() for t in themes],
            },
            indent=2,
            ensure_ascii=False,
        )

    header = (
        f"# 🎯 Clustered Themes Across {total_interviews} Interviews"
        if normalized == "notion"
        else f"# Clustered Themes Across {total_interviews} Interviews"
    )
    lines = [header, ""]
    if not themes:
        lines.append("_No recurring clusters detected at the configured similarity threshold._")
        return "\n".join(lines) + "\n"

    for idx, theme in enumerate(themes, start=1):
        pct = f"{theme.prevalence_pct:.0f}%" if theme.total_interviews > 0 else "N/A"
        lines.append(
            f"## {idx}. {theme.theme_name} "
            f"({theme.frequency} interviews, {pct} — highest severity: "
            f"{theme.highest_severity.value.upper()})"
        )
        lines.append("")
        for pp in theme.pain_points:
            lines.append(f"- [{pp.interview_id}] {pp.description}")
        if theme.representative_quote:
            lines.append("")
            lines.append(f'> Representative quote: "{theme.representative_quote}"')
        lines.append("")

    return "\n".join(lines)


@dataclass
class DashboardSnapshot:
    """Aggregated synthesis dashboard metrics over time."""

    total_interviews: int
    total_pain_points: int
    total_jtbd: int
    total_quotes: int
    total_pii_redacted: int
    severity_distribution: dict[str, int]
    trending_themes: list[dict[str, Any]]
    insights_over_time: list[dict[str, Any]]
    review_status_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_interviews": self.total_interviews,
            "total_pain_points": self.total_pain_points,
            "total_jtbd": self.total_jtbd,
            "total_quotes": self.total_quotes,
            "total_pii_redacted": self.total_pii_redacted,
            "severity_distribution": dict(self.severity_distribution),
            "trending_themes": list(self.trending_themes),
            "insights_over_time": list(self.insights_over_time),
            "review_status_counts": dict(self.review_status_counts),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Interview Synthesis Dashboard",
            "",
            f"- **Interviews Synthesized:** {self.total_interviews}",
            f"- **Pain Points Extracted:** {self.total_pain_points}",
            f"- **Jobs-to-be-Done Extracted:** {self.total_jtbd}",
            f"- **Traceable Quotes:** {self.total_quotes}",
            f"- **PII Items Redacted:** {self.total_pii_redacted}",
            "",
            "## Severity Distribution",
            "",
        ]
        for sev, count in self.severity_distribution.items():
            lines.append(f"- **{sev.upper()}**: {count}")

        lines.extend(["", "## Trending Themes", ""])
        for item in self.trending_themes:
            lines.append(
                f"- **{item['theme']}**: {item['interview_count']} interview(s) "
                f"({item['prevalence_pct']:.0f}%)"
            )

        lines.extend(["", "## Insights Over Time", ""])
        lines.append("| Date | Interviews | Pain Points | JTBD |")
        lines.append("| :--- | :---: | :---: | :---: |")
        for bucket in self.insights_over_time:
            lines.append(
                f"| {bucket['date']} | {bucket['interviews']} | "
                f"{bucket['pain_points']} | {bucket['jtbd']} |"
            )
        lines.append("")
        return "\n".join(lines)


class SynthesisDashboard:
    """Aggregate synthesized interviews into insights-over-time and trending theme metrics."""

    def build_snapshot(self, insights_list: list[InterviewInsights]) -> DashboardSnapshot:
        total_interviews = len(insights_list)
        severity_counts: dict[str, int] = {s.value: 0 for s in PainPointSeverity}
        theme_interviews: dict[str, set[str]] = defaultdict(set)
        timeline: dict[str, dict[str, int]] = defaultdict(
            lambda: {"interviews": 0, "pain_points": 0, "jtbd": 0}
        )
        review_counts: Counter[str] = Counter()

        total_pain_points = 0
        total_jtbd = 0
        total_quotes = 0
        total_pii = 0

        for item in insights_list:
            total_pain_points += len(item.pain_points)
            total_jtbd += len(item.jobs_to_be_done)
            total_quotes += len(item.quotes)
            total_pii += sum(item.redaction_counts.values())
            review_counts[item.review_status.value] += 1

            for pp in item.pain_points:
                severity_counts[pp.severity.value] = severity_counts.get(pp.severity.value, 0) + 1

            for theme in item.themes:
                theme_interviews[theme.strip()].add(item.interview_id)

            date_key = item.created_at[:10] if item.created_at else "unknown"
            timeline[date_key]["interviews"] += 1
            timeline[date_key]["pain_points"] += len(item.pain_points)
            timeline[date_key]["jtbd"] += len(item.jobs_to_be_done)

        trending = sorted(
            [
                {
                    "theme": theme,
                    "interview_count": len(ids),
                    "prevalence_pct": round((len(ids) / total_interviews) * 100.0, 1)
                    if total_interviews
                    else 0.0,
                }
                for theme, ids in theme_interviews.items()
                if theme
            ],
            key=lambda row: (row["interview_count"], row["theme"]),
            reverse=True,
        )

        over_time = [
            {"date": date_key, **counts}
            for date_key, counts in sorted(timeline.items())
        ]

        return DashboardSnapshot(
            total_interviews=total_interviews,
            total_pain_points=total_pain_points,
            total_jtbd=total_jtbd,
            total_quotes=total_quotes,
            total_pii_redacted=total_pii,
            severity_distribution=severity_counts,
            trending_themes=trending,
            insights_over_time=over_time,
            review_status_counts=dict(review_counts),
        )
