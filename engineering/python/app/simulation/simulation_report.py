"""Simulation result report generator.

Aggregates collision detection results and visualization outputs into
structured simulation reports. Supports both JSON serialization and
human-readable text summary generation.

Example:
    >>> report = SimulationReport.from_validation(
    ...     segments=parsed_segments,
    ...     collision_report=collision_report,
    ...     visualization_path="output/viz.png",
    ... )
    >>> report.save_json("output/report.json")
    >>> print(generate_summary_text(report))
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.simulation.collision_detector import CollisionReport
from app.simulation.toolpath_parser import ToolpathSegment


@dataclass
class SimulationReport:
    """Complete simulation report containing all validation results.

    Consolidates toolpath statistics, collision analysis, and visualization
    output paths into a single reportable structure.

    Attributes:
        timestamp: ISO 8601 timestamp of report generation.
        total_segments: Total number of toolpath segments processed.
        rapid_segments: Number of G00 rapid move segments.
        linear_segments: Number of G01 linear interpolation segments.
        arc_segments: Number of G02/G03 circular interpolation segments.
        dwell_segments: Number of G04 dwell segments.
        collision_report: Detailed collision detection results.
        visualization_path: File path to the toolpath visualization image.
        duration_seconds: Total simulation execution time.
        part_name: Name of the machined part.
    """

    timestamp: str
    total_segments: int
    rapid_segments: int
    linear_segments: int
    arc_segments: int
    dwell_segments: int
    collision_report: CollisionReport
    visualization_path: str = ""
    duration_seconds: float = 0.0
    part_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert the report to a dictionary for serialization.

        Returns:
            Dictionary with all report fields and a PASS/FAIL status.
        """
        return {
            "timestamp": self.timestamp,
            "total_segments": self.total_segments,
            "rapid_segments": self.rapid_segments,
            "linear_segments": self.linear_segments,
            "arc_segments": self.arc_segments,
            "dwell_segments": self.dwell_segments,
            "collision_report": self.collision_report.to_dict(),
            "visualization_path": self.visualization_path,
            "duration_seconds": round(self.duration_seconds, 3),
            "part_name": self.part_name,
            "status": "PASS" if self.collision_report.safe else "FAIL",
        }

    def save_json(self, output_path: str) -> str:
        """Save the report as a JSON file.

        Creates parent directories if they do not exist.

        Args:
            output_path: Destination file path for the JSON report.

        Returns:
            The absolute path of the saved JSON file.
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return str(out)

    @classmethod
    def from_validation(
        cls,
        segments: list[ToolpathSegment],
        collision_report: CollisionReport,
        visualization_path: str = "",
        duration_seconds: float = 0.0,
        part_name: str = "",
    ) -> "SimulationReport":
        """Create a report from toolpath segments and collision results.

        Automatically counts segment types and generates a UTC timestamp.

        Args:
            segments: List of parsed toolpath segments.
            collision_report: Collision detection results.
            visualization_path: Path to the visualization output file.
            duration_seconds: Simulation execution time in seconds.
            part_name: Name of the machined part.

        Returns:
            A populated SimulationReport instance.
        """
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_segments=len(segments),
            rapid_segments=sum(1 for s in segments if s.type == "rapid"),
            linear_segments=sum(1 for s in segments if s.type == "linear"),
            arc_segments=sum(1 for s in segments if s.type == "arc"),
            dwell_segments=sum(1 for s in segments if s.type == "dwell"),
            collision_report=collision_report,
            visualization_path=visualization_path,
            duration_seconds=duration_seconds,
            part_name=part_name,
        )


def generate_summary_text(report: SimulationReport) -> str:
    """Generate a human-readable text summary of the simulation report.

    Produces a formatted text report including part name, timing,
    segment statistics, safety status, and detailed collision information.

    Args:
        report: The simulation report to summarize.

    Returns:
        Formatted text summary string suitable for console output or logs.
    """
    cr = report.collision_report
    status = "Safe" if cr.safe else "Collision detected"
    lines = [
        "=" * 60,
        f"  NC Toolpath Simulation Report - {report.part_name or 'Unnamed'}",
        "=" * 60,
        f"  Generated: {report.timestamp}",
        f"  Simulation time: {report.duration_seconds:.2f}s",
        "",
        "  Segment statistics:",
        f"    Total: {report.total_segments}",
        f"    G00 Rapid: {report.rapid_segments}",
        f"    G01 Linear: {report.linear_segments}",
        f"    G02/G03 Arc: {report.arc_segments}",
        f"    G04 Dwell: {report.dwell_segments}",
        "",
        f"  Safety status: {status}",
        f"  Collision events: {len(cr.collisions)}",
    ]

    for c in cr.collisions:
        lines.append(f"    [{c.severity.upper()}] N{c.block_number} {c.collision_type}: {c.message}")

    if cr.warnings:
        lines.append("")
        lines.append("  Boundary warnings:")
        for w in cr.warnings:
            lines.append(f"    - {w}")

    return "\n".join(lines)
