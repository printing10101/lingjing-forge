"""Toolpath 3D visualization module.

Provides multi-angle visualization of CNC toolpaths using matplotlib 3D,
supporting both PNG static images and HTML interactive output formats.

The visualizer renders:
    - Stock bounding box as a wireframe
    - Toolpath segments colored by motion type (G00=red, G01=green, arc=blue, dwell=yellow)
    - Axis labels and coordinate system
    - Interactive rotation/zoom in HTML output via Three.js

Example:
    >>> viz = ToolpathVisualizer(stock_model)
    >>> viz.render_png(segments, "output/toolpath.png")
    >>> viz.render_html(segments, "output/toolpath.html")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from app.simulation.constants import (
    AXIS_LABEL_X,
    AXIS_LABEL_Y,
    AXIS_LABEL_Z,
    COLOR_ARC_MOVE,
    COLOR_DEFAULT_SEGMENT,
    COLOR_DWELL,
    COLOR_LINEAR_MOVE,
    COLOR_RAPID_MOVE,
    COLOR_STOCK,
    DEFAULT_X_LIM,
    DEFAULT_Y_LIM,
    DEFAULT_Z_LIM,
    DPI_PNG,
    FIGSIZE_DEFAULT,
    FONT_SIZE_LEGEND,
    FONT_SIZE_TITLE,
    LEGEND_LOC,
    LINE_WIDTH_STOCK_WIREFRAME,
    LINE_WIDTH_TOOLPATH,
    STOCK_PADDING,
    STOCK_Z_PADDING_EXTRA,
    SUBPLOT_ADJUST,
    TITLE_TEXT,
)
from app.simulation.stock_model import StockModel
from app.simulation.toolpath_parser import ToolpathSegment


# 模板加载

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# 图例 HTML 块（仅当 matplotlib axes 存在 legend 时注入）。
# 运动类型颜色由 constants 注入，确保与 _draw_segments 中颜色一致。
_LEGEND_HTML = (
    "<p>Legend: <b style='color:{rapid}'>G00 Rapid</b> | "
    "<b style='color:{linear}'>G01 Linear</b> | "
    "<b style='color:{arc}'>G02/G03 Arc</b> | "
    "<b style='color:{dwell}'>G04 Dwell</b></p>"
).format(
    rapid=COLOR_RAPID_MOVE,
    linear=COLOR_LINEAR_MOVE,
    arc=COLOR_ARC_MOVE,
    dwell=COLOR_DWELL,
)


def _load_template(name: str) -> str:
    """从 templates 目录读取指定模板文件内容。

    Args:
        name: 模板文件名（如 ``toolpath_viewer.html``）。

    Returns:
        str: 模板文件的文本内容。
    """
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


class ToolpathVisualizer:
    """3D visualizer for CNC toolpath segments and stock geometry.

    Renders toolpaths with color-coded motion types and optional stock
    bounding box wireframe. Supports static PNG output and interactive
    HTML output with Three.js-based 3D navigation.

    Attributes:
        stock: Optional stock model for rendering the workpiece boundary.

    Example:
        >>> viz = ToolpathVisualizer(stock)
        >>> viz.render_png(segments, "output/view.png")
    """

    def __init__(self, stock: StockModel | None = None) -> None:
        """Initialize the visualizer with an optional stock model.

        Args:
            stock: Stock model to render as a bounding box. If None,
                only the toolpath is rendered.
        """
        self.stock = stock

    def render_png(
        self,
        segments: list[ToolpathSegment],
        output_path: str,
    ) -> str:
        """Render the toolpath as a static PNG image.

        Creates a 3D matplotlib plot with stock wireframe (if available),
        color-coded toolpath segments, axis labels, and title.

        Args:
            segments: List of toolpath segments to render.
            output_path: Destination file path for the PNG image.

        Returns:
            Absolute path to the saved PNG file.
        """
        fig = plt.figure(figsize=FIGSIZE_DEFAULT)
        ax = fig.add_subplot(111, projection="3d")
        self._draw_stock(ax)
        self._draw_segments(ax, segments)
        self._apply_labels(ax)
        plt.subplots_adjust(
            left=SUBPLOT_ADJUST[0],
            right=SUBPLOT_ADJUST[1],
            top=SUBPLOT_ADJUST[2],
            bottom=SUBPLOT_ADJUST[3],
        )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out), dpi=DPI_PNG, bbox_inches="tight")
        plt.close(fig)
        return str(out)

    def render_html(
        self,
        segments: list[ToolpathSegment],
        output_path: str,
    ) -> str:
        """Render the toolpath as an interactive HTML page.

        Generates a self-contained HTML file with Three.js-based 3D
        viewer supporting mouse rotation, zoom, and pan controls.

        Args:
            segments: List of toolpath segments to render.
            output_path: Destination file path for the HTML file.

        Returns:
            Absolute path to the saved HTML file.
        """
        fig = plt.figure(figsize=FIGSIZE_DEFAULT)
        ax = fig.add_subplot(111, projection="3d")
        self._draw_stock(ax)
        self._draw_segments(ax, segments)
        self._apply_labels(ax)

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        html = self._build_interactive_html(fig, ax)
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        plt.close(fig)
        return str(out)

    def _draw_stock(self, ax: Any) -> None:
        """Draw the stock bounding box as a wireframe.

        Renders the top, bottom, and side faces of the stock bounding
        box as semi-transparent gray surfaces.

        Args:
            ax: Matplotlib 3D axes object.
        """
        if self.stock is None:
            return
        bbox = self.stock.get_bbox()
        x = [bbox.x_min, bbox.x_max]
        y = [bbox.y_min, bbox.y_max]
        z = [bbox.z_min, bbox.z_max]
        xx, yy = np.meshgrid(x, y)

        ax.plot_surface(xx, yy, np.full_like(xx, z[0]), alpha=0.2, color=COLOR_STOCK)
        ax.plot_surface(xx, yy, np.full_like(xx, z[1]), alpha=0.15, color=COLOR_STOCK)

        for z_plane in z:
            ax.plot_wireframe(
                xx,
                yy,
                np.full_like(xx, z_plane),
                color=COLOR_STOCK,
                alpha=0.4,
                linewidth=LINE_WIDTH_STOCK_WIREFRAME,
            )

        for y_val in y:
            xz_x, xz_z = np.meshgrid(x, z)
            ax.plot_surface(xz_x, np.full_like(xz_x, y_val), xz_z, alpha=0.1, color=COLOR_STOCK)

        for x_val in x:
            yz_y, yz_z = np.meshgrid(y, z)
            ax.plot_surface(np.full_like(yz_y, x_val), yz_y, yz_z, alpha=0.1, color=COLOR_STOCK)

    def _draw_segments(self, ax: Any, segments: list[ToolpathSegment]) -> None:
        """Draw all toolpath segments with color coding by motion type.

        Colors:
            - rapid (G00): Red
            - linear (G01): Green
            - arc (G02/G03): Blue
            - dwell (G04): Yellow

        Args:
            ax: Matplotlib 3D axes object.
            segments: List of toolpath segments to draw.
        """
        colors = {
            "rapid": COLOR_RAPID_MOVE,
            "linear": COLOR_LINEAR_MOVE,
            "arc": COLOR_ARC_MOVE,
            "dwell": COLOR_DWELL,
        }
        labels_used: set[str] = set()

        for seg in segments:
            c = colors.get(seg.type, COLOR_DEFAULT_SEGMENT)
            lbl = seg.type if seg.type not in labels_used else None
            if lbl:
                labels_used.add(lbl)

            sx, sy, sz = seg.start_point
            ex, ey, ez = seg.end_point

            ax.plot(
                [sx, ex],
                [sy, ey],
                [sz, ez],
                color=c,
                linewidth=LINE_WIDTH_TOOLPATH,
                label=lbl,
            )

            if seg.type == "rapid":
                ax.scatter(ex, ey, ez, color=c, s=8, marker="o", alpha=0.7)
            elif seg.type == "dwell":
                ax.scatter(ex, ey, ez, color=c, s=30, marker="s", alpha=0.8)

        if labels_used:
            ax.legend(loc=LEGEND_LOC, fontsize=FONT_SIZE_LEGEND)

    def _apply_labels(self, ax: Any) -> None:
        """Apply axis labels, title, and view limits.

        Sets X/Y/Z labels in mm, applies a Chinese title for the
        visualization, and adjusts the view range based on stock
        dimensions or defaults.

        Args:
            ax: Matplotlib 3D axes object.
        """
        ax.set_xlabel(AXIS_LABEL_X)
        ax.set_ylabel(AXIS_LABEL_Y)
        ax.set_zlabel(AXIS_LABEL_Z)
        ax.set_title(TITLE_TEXT, fontsize=FONT_SIZE_TITLE)

        if self.stock:
            bbox = self.stock.get_bbox()
            ax.set_xlim(bbox.x_min - STOCK_PADDING, bbox.x_max + STOCK_PADDING)
            ax.set_ylim(bbox.y_min - STOCK_PADDING, bbox.y_max + STOCK_PADDING)
            ax.set_zlim(
                bbox.z_min - STOCK_PADDING,
                bbox.z_max + STOCK_PADDING + STOCK_Z_PADDING_EXTRA,
            )
        else:
            ax.set_xlim(*DEFAULT_X_LIM)
            ax.set_ylim(*DEFAULT_Y_LIM)
            ax.set_zlim(*DEFAULT_Z_LIM)

    def _build_interactive_html(self, fig: Any, ax: Any) -> str:
        """Build a self-contained interactive HTML page with Three.js.

        Generates HTML with embedded Three.js scene, camera, lighting,
        and orbit controls for interactive 3D toolpath viewing.

        Args:
            fig: Matplotlib figure (used for legend extraction).
            ax: Matplotlib 3D axes (used for legend extraction).

        Returns:
            Complete HTML string for the interactive viewer page.
        """
        legend_html = _LEGEND_HTML if ax.get_legend_handles_labels()[0] else ""
        template = _load_template("toolpath_viewer.html")
        return template.format(legend_html=legend_html)
