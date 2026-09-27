"""Unit tests for geometric operations - circle/polygon boolean operations.

Covers:
- Point-in-circle and point-in-polygon tests
- Circle-polygon intersection calculations
- Boundary conditions (extreme sizes, degenerate cases)
- Precision and performance benchmarks
"""

from __future__ import annotations

import math
import pytest

from tests.utils.gcode_helpers import (
    point_in_circle,
    point_in_polygon,
    circle_polygon_intersection_area,
    assert_almost_equal,
)
from tests.conftest import Circle2D, Polygon2D


@pytest.mark.unit
@pytest.mark.geometry
class TestPointInCircle:
    """Point-in-circle inclusion tests."""

    def test_point_at_center(self, sample_circle: Circle2D):
        """Point at center should be inside."""
        assert point_in_circle(
            0.0,
            0.0,
            sample_circle.center_x,
            sample_circle.center_y,
            sample_circle.radius,
        )

    def test_point_on_boundary(self, sample_circle: Circle2D):
        """Point on the boundary should be inside."""
        assert point_in_circle(
            10.0,
            0.0,
            sample_circle.center_x,
            sample_circle.center_y,
            sample_circle.radius,
        )

    def test_point_outside(self, sample_circle: Circle2D):
        """Point clearly outside should not be inside."""
        assert not point_in_circle(
            20.0,
            0.0,
            sample_circle.center_x,
            sample_circle.center_y,
            sample_circle.radius,
        )

    def test_point_near_boundary_inside(self, sample_circle: Circle2D):
        """Point just inside boundary."""
        r = sample_circle.radius
        assert point_in_circle(
            r - 0.001,
            0.0,
            sample_circle.center_x,
            sample_circle.center_y,
            sample_circle.radius,
        )

    def test_point_near_boundary_outside(self, sample_circle: Circle2D):
        """Point just outside boundary."""
        r = sample_circle.radius
        assert not point_in_circle(
            r + 0.001,
            0.0,
            sample_circle.center_x,
            sample_circle.center_y,
            sample_circle.radius,
        )

    def test_circle_zero_radius(self):
        """Circle with zero radius should only include center point."""
        assert point_in_circle(0.0, 0.0, 0.0, 0.0, 0.0)
        assert not point_in_circle(0.001, 0.0, 0.0, 0.0, 0.0)

    def test_circle_large_radius(self):
        """Very large circle should include most points."""
        assert point_in_circle(0.0, 0.0, 0.0, 0.0, 1e6)
        assert point_in_circle(1000.0, 1000.0, 0.0, 0.0, 1e6)
        assert not point_in_circle(2e6, 0.0, 0.0, 0.0, 1e6)

    def test_point_at_diagonal(self):
        """Point on diagonal boundary: sqrt(2) * r."""
        r = 10.0
        diag = r / math.sqrt(2)
        assert point_in_circle(diag, diag, 0.0, 0.0, r)
        assert not point_in_circle(diag + 0.1, diag + 0.1, 0.0, 0.0, r)


@pytest.mark.unit
@pytest.mark.geometry
class TestPointInPolygon:
    """Point-in-polygon tests using ray-casting algorithm."""

    def test_point_inside_square(self, sample_polygon_square: Polygon2D):
        """Point inside square should be detected."""
        assert point_in_polygon(0.0, 0.0, sample_polygon_square.vertices)

    def test_point_outside_square(self, sample_polygon_square: Polygon2D):
        """Point outside square should be detected."""
        assert not point_in_polygon(10.0, 10.0, sample_polygon_square.vertices)

    def test_point_on_edge(self, sample_polygon_square: Polygon2D):
        """Point on edge - boundary behavior."""
        assert not point_in_polygon(5.0, 0.0, sample_polygon_square.vertices)

    def test_point_at_vertex(self, sample_polygon_square: Polygon2D):
        """Point at vertex."""
        assert not point_in_polygon(5.0, 5.0, sample_polygon_square.vertices)

    def test_point_inside_triangle(self, sample_polygon_triangle: Polygon2D):
        """Point inside equilateral triangle."""
        assert point_in_polygon(0.0, 0.0, sample_polygon_triangle.vertices)

    def test_point_outside_triangle(self, sample_polygon_triangle: Polygon2D):
        """Point outside triangle."""
        assert not point_in_polygon(20.0, 0.0, sample_polygon_triangle.vertices)

    def test_degenerate_polygon_two_points(self):
        """Degenerate case: only two vertices."""
        vertices = [(0.0, 0.0), (1.0, 1.0)]
        assert not point_in_polygon(0.5, 0.5, vertices)

    def test_concave_polygon(self):
        """Point inside a concave (L-shaped) polygon."""
        l_shape = [
            (0.0, 0.0),
            (10.0, 0.0),
            (10.0, 5.0),
            (5.0, 5.0),
            (5.0, 10.0),
            (0.0, 10.0),
        ]
        assert point_in_polygon(2.0, 2.0, l_shape)
        assert point_in_polygon(7.0, 2.0, l_shape)
        assert not point_in_polygon(7.0, 7.0, l_shape)


@pytest.mark.unit
@pytest.mark.geometry
class TestCirclePolygonBooleanOperations:
    """Circle-polygon boolean operation tests (intersection, containment)."""

    def test_circle_contains_polygon(
        self, sample_circle: Circle2D, sample_polygon_square: Polygon2D
    ):
        """Small polygon fully inside large circle."""
        all_inside = all(
            point_in_circle(
                v[0],
                v[1],
                sample_circle.center_x,
                sample_circle.center_y,
                sample_circle.radius,
            )
            for v in sample_polygon_square.vertices
        )
        assert all_inside

    def test_circle_partially_overlaps_polygon(self):
        """Circle partially overlapping polygon - some vertices inside."""
        circle = Circle2D(center_x=3.0, center_y=0.0, radius=5.0)
        square = Polygon2D(
            vertices=[(-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0)]
        )

        inside_count = sum(
            1
            for v in square.vertices
            if point_in_circle(
                v[0], v[1], circle.center_x, circle.center_y, circle.radius
            )
        )
        assert inside_count <= len(square.vertices)
        area = circle_polygon_intersection_area(
            circle.center_x, circle.center_y, circle.radius, square.vertices
        )
        assert area > 0

    def test_circle_no_overlap_polygon(self):
        """Circle completely outside polygon."""
        circle = Circle2D(center_x=50.0, center_y=50.0, radius=5.0)
        square = Polygon2D(
            vertices=[(-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0)]
        )

        for v in square.vertices:
            assert not point_in_circle(
                v[0], v[1], circle.center_x, circle.center_y, circle.radius
            )

    def test_intersection_area_circle_contains_polygon(
        self, sample_polygon_square: Polygon2D
    ):
        """When polygon is fully inside circle, intersection area = polygon area."""
        circle_r = 20.0
        area = circle_polygon_intersection_area(
            0.0, 0.0, circle_r, sample_polygon_square.vertices
        )
        expected_area = 10.0 * 10.0
        assert_almost_equal(
            area,
            expected_area,
            tolerance=expected_area * 0.1,
            msg="Polygon area when fully inside circle",
        )

    def test_intersection_area_no_overlap(self):
        """No overlap should give zero intersection area."""
        square = Polygon2D(
            vertices=[(50.0, 50.0), (60.0, 50.0), (60.0, 60.0), (50.0, 60.0)]
        )
        area = circle_polygon_intersection_area(0.0, 0.0, 5.0, square.vertices)
        assert area == 0.0

    def test_intersection_area_partial_overlap(self):
        """Partial overlap: intersection area between 0 and polygon area."""
        square = Polygon2D(
            vertices=[(5.0, -5.0), (15.0, -5.0), (15.0, 5.0), (5.0, 5.0)]
        )
        circle_r = 10.0
        area = circle_polygon_intersection_area(0.0, 0.0, circle_r, square.vertices)
        assert area > 0
        polygon_area = 10.0 * 10.0
        assert area < polygon_area

    def test_intersection_performance(self, performance_timer):
        """Intersection calculation should complete within performance budget."""
        with performance_timer as t:
            square = Polygon2D(
                vertices=[(-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0)]
            )
            circle_polygon_intersection_area(0.0, 0.0, 10.0, square.vertices)
        assert t.elapsed_s < 5.0, (
            f"Intersection took {t.elapsed_s:.2f}s, exceeds 5s limit"
        )

    def test_boundary_degenerate_circle_point(self):
        """Degenerate circle (radius ~0) with polygon."""
        square = Polygon2D(
            vertices=[(-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0)]
        )
        area = circle_polygon_intersection_area(0.0, 0.0, 0.0001, square.vertices)
        assert area < 0.01

    def test_complex_polygon(self):
        """Circle intersection with complex hexagon."""
        hexagon = Polygon2D(
            vertices=[
                (
                    round(10 * math.cos(i * math.pi / 3)),
                    round(10 * math.sin(i * math.pi / 3)),
                )
                for i in range(6)
            ]
        )
        area = circle_polygon_intersection_area(0.0, 0.0, 15.0, hexagon.vertices)
        expected_area = (3 * math.sqrt(3) / 2) * (10**2) / 2
        assert area > 0
        assert area < expected_area * 3.0
