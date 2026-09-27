"""Stock model management module.

Supports rectangular and cylindrical stock types with bounding-box
querying and coordinate transformation interfaces.

Coordinate convention:
    - X/Y: horizontal plane
    - Z: vertical axis (positive upward)
    - Stock bottom is placed at Z=0

Example:
    >>> stock = StockModel(length=200, width=150, height=50)
    >>> bbox = stock.get_bbox()
    >>> print(f"Volume: {bbox.volume():.1f} mm³")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StockBoundingBox:
    """Axis-aligned bounding box defining the stock geometry extents.

    Attributes:
        x_min: Minimum X coordinate (mm).
        x_max: Maximum X coordinate (mm).
        y_min: Minimum Y coordinate (mm).
        y_max: Maximum Y coordinate (mm).
        z_min: Minimum Z coordinate (mm).
        z_max: Maximum Z coordinate (mm).
    """

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

    def contains_point(self, x: float, y: float, z: float) -> bool:
        """Check if a point lies within this bounding box.

        Args:
            x: X coordinate.
            y: Y coordinate.
            z: Z coordinate.

        Returns:
            True if the point is inside or on the boundary of the box.
        """
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max and self.z_min <= z <= self.z_max

    def intersects_bbox(self, other: "StockBoundingBox") -> bool:
        """Check if this bounding box intersects another.

        Uses the separating axis theorem: two AABBs intersect if and only if
        they overlap on all three axes.

        Args:
            other: The other bounding box to test against.

        Returns:
            True if the two boxes overlap (including touching).
        """
        return not (
            other.x_max < self.x_min
            or other.x_min > self.x_max
            or other.y_max < self.y_min
            or other.y_min > self.y_max
            or other.z_max < self.z_min
            or other.z_min > self.z_max
        )

    def volume(self) -> float:
        """Calculate the volume of the bounding box.

        Returns:
            Volume in cubic millimeters.
        """
        return (self.x_max - self.x_min) * (self.y_max - self.y_min) * (self.z_max - self.z_min)

    def center(self) -> tuple[float, float, float]:
        """Get the center point of the bounding box.

        Returns:
            (x, y, z) coordinates of the box center.
        """
        return (
            (self.x_min + self.x_max) / 2,
            (self.y_min + self.y_max) / 2,
            (self.z_min + self.z_max) / 2,
        )

    def to_dict(self) -> dict[str, float]:
        """Convert the bounding box to a dictionary.

        Returns:
            Dictionary with x_min, x_max, y_min, y_max, z_min, z_max,
            volume, and center keys.
        """
        cx, cy, cz = self.center()
        return {
            "x_min": self.x_min,
            "x_max": self.x_max,
            "y_min": self.y_min,
            "y_max": self.y_max,
            "z_min": self.z_min,
            "z_max": self.z_max,
            "volume": self.volume(),
            "center_x": cx,
            "center_y": cy,
            "center_z": cz,
        }


class StockModel:
    """Rectangular stock model.

    Defines a cuboid stock with geometric dimensions and bounding box.
    The stock is centered on X/Y with its bottom face at Z=0.

    Attributes:
        length: Stock length along X axis (mm).
        width: Stock width along Y axis (mm).
        height: Stock height along Z axis (mm).
        origin_x: X coordinate of the stock center.
        origin_y: Y coordinate of the stock center.

    Example:
        >>> stock = StockModel(length=200, width=150, height=50)
        >>> print(stock.contains_point(0, 0, 25))  # True
        >>> bbox = stock.get_bbox()
    """

    def __init__(
        self,
        length: float = 200,
        width: float = 150,
        height: float = 50,
        x_offset: float = 0.0,
        y_offset: float = 0.0,
        z_offset: float = 0.0,
    ) -> None:
        """Initialize a rectangular stock model.

        Args:
            length: Stock length along X axis in mm.
            width: Stock width along Y axis in mm.
            height: Stock height along Z axis in mm.
            x_offset: X 方向偏移（平移毛坯中心，默认 0）。
            y_offset: Y 方向偏移（平移毛坯中心，默认 0）。
            z_offset: Z 方向偏移（平移毛坯底面，默认 0）。
        """
        self.length = length
        self.width = width
        self.height = height
        self.origin_x = x_offset
        self.origin_y = y_offset
        self.origin_z = z_offset

    def set_dimensions(
        self,
        length: float,
        width: float,
        height: float,
    ) -> None:
        """Update the stock dimensions.

        Args:
            length: New stock length along X axis in mm.
            width: New stock width along Y axis in mm.
            height: New stock height along Z axis in mm.
        """
        self.length = length
        self.width = width
        self.height = height

    def get_bbox(self) -> StockBoundingBox:
        """Get the bounding box of the stock.

        Returns:
            StockBoundingBox centered on origin_x/origin_y with bottom at Z=0.
        """
        hl = self.length / 2
        hw = self.width / 2
        return StockBoundingBox(
            x_min=self.origin_x - hl,
            x_max=self.origin_x + hl,
            y_min=self.origin_y - hw,
            y_max=self.origin_y + hw,
            z_min=self.origin_z,
            z_max=self.origin_z + self.height,
        )

    def contains_point(self, x: float, y: float, z: float) -> bool:
        """Check if a point is inside the stock volume.

        Args:
            x: X coordinate.
            y: Y coordinate.
            z: Z coordinate.

        Returns:
            True if the point is within the stock boundaries.
        """
        return self.get_bbox().contains_point(x, y, z)

    def to_dict(self) -> dict[str, Any]:
        """Convert the stock model to a dictionary.

        Returns:
            Dictionary with stock type, dimensions, origin, and bounding box.
        """
        return {
            "type": "rectangular",
            "length": self.length,
            "width": self.width,
            "height": self.height,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "origin_z": self.origin_z,
            "bbox": self.get_bbox().to_dict(),
        }


class CylindricalStock(StockModel):
    """Cylindrical stock model.

    Defines a cylindrical stock with its axis along the Z direction.
    Inherits from StockModel for compatibility with the collision
    detection system.

    Attributes:
        diameter: Stock diameter in mm.
        height: Stock height along Z axis in mm.

    Example:
        >>> stock = CylindricalStock(diameter=100, height=200)
        >>> print(stock.contains_point(0, 0, 100))  # True
    """

    def __init__(
        self,
        diameter: float = 100,
        height: float = 200,
    ) -> None:
        """Initialize a cylindrical stock model.

        Args:
            diameter: Stock diameter in mm.
            height: Stock height along Z axis in mm.
        """
        self.diameter = diameter
        super().__init__(length=diameter, width=diameter, height=height)

    def set_dimensions(  # type: ignore[override]
        self,
        diameter: float,
        height: float,
    ) -> None:
        """Update the cylindrical stock dimensions.

        Args:
            diameter: New stock diameter in mm.
            height: New stock height along Z axis in mm.
        """
        self.diameter = diameter
        self.length = diameter
        self.width = diameter
        self.height = height

    def get_bbox(self) -> StockBoundingBox:
        """Get the bounding box that encloses the cylinder.

        Returns:
            StockBoundingBox that circumscribes the cylindrical stock.
        """
        r = self.diameter / 2
        return StockBoundingBox(
            x_min=self.origin_x - r,
            x_max=self.origin_x + r,
            y_min=self.origin_y - r,
            y_max=self.origin_y + r,
            z_min=0.0,
            z_max=self.height,
        )

    def contains_point(self, x: float, y: float, z: float) -> bool:
        """Check if a point is inside the cylindrical stock.

        Uses the radial distance from the center axis and Z height check
        with a small tolerance (0.001mm).

        Args:
            x: X coordinate.
            y: Y coordinate.
            z: Z coordinate.

        Returns:
            True if the point is within the cylinder volume.
        """
        r = self.diameter / 2
        dx = x - self.origin_x
        dy = y - self.origin_y
        if dx * dx + dy * dy > r * r + 0.001:
            return False
        return 0.0 <= z <= self.height + 0.001

    def to_dict(self) -> dict[str, Any]:
        """Convert the cylindrical stock to a dictionary.

        Returns:
            Dictionary with stock type, diameter, height, origin, and bounding box.
        """
        return {
            "type": "cylindrical",
            "diameter": self.diameter,
            "height": self.height,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "bbox": self.get_bbox().to_dict(),
        }
