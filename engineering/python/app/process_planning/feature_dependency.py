"""Machining feature dependency graph.

Establishes geometric and manufacturing relationships between machining features,
enabling topology-sort-driven optimal machining sequences. Follows manufacturing
principles: 'face before hole', 'rough before finish', 'primary before secondary',
and 'datum first'.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MachiningFeature:
    """A machining feature with geometric and process attributes.

    Attributes:
        name: Feature name, e.g. 'H001', 'Surface A'.
        type: Feature type, e.g. 'through_hole', 'plane_surface', 'end_face'.
        geometric_type: Geometric classification, e.g. 'cylinder', 'plane'.
        tolerance_grade: IT tolerance grade, e.g. 'IT8'.
        surface_roughness_ra: Surface roughness Ra value (um).
        is_datum_candidate: Whether this feature can serve as a datum.
        machining_method: Specific machining method string.
        priority: Feature priority 'high'/'medium'/'low'.
        surface: Surface identifier this feature belongs to.
        dimensions: Dimension dictionary {key: value in mm}.
        parent_feature: Parent feature name for nested features.
        tolerances: Tolerance values dictionary.
    """

    name: str
    type: str
    geometric_type: str = ""
    tolerance_grade: str = "IT8"
    surface_roughness_ra: float = 6.3
    is_datum_candidate: bool = False
    machining_method: str = ""
    priority: str = "medium"
    surface: str = "A"
    dimensions: dict[str, float] = field(default_factory=dict)
    parent_feature: str = ""
    tolerances: dict[str, float] = field(default_factory=dict)

    def is_rough(self) -> bool:
        """Check whether the feature requires rough machining.

        Returns:
            True if the tolerance grade is IT10 or coarser.
        """
        return self.tolerance_grade in ("IT10", "IT11", "IT12", "IT13", "IT14")

    def is_finish(self) -> bool:
        """Check whether the feature requires finish machining.

        Returns:
            True if the tolerance grade is IT7 or finer.
        """
        return self.tolerance_grade in ("IT5", "IT6", "IT7")

    def is_hole(self) -> bool:
        """Check whether the feature is a hole-type feature.

        Returns:
            True if the geometric type is 'cylinder' and the type is a hole variant.
        """
        return self.geometric_type in ("cylinder",) and self.type in (
            "through_hole",
            "inner_bore",
            "counterbore",
            "center_hole",
        )

    def is_face(self) -> bool:
        """Check whether the feature is a planar face.

        Returns:
            True if the geometric type is 'plane'.
        """
        return self.geometric_type == "plane"

    def priority_score(self) -> int:
        """Calculate a priority score for machining sequence ordering.

        Higher scores indicate features that should be machined earlier.
        Datum features get the highest score, followed by end faces,
        then precision features, then planes. Holes and secondary
        features get negative adjustments.

        Returns:
            Priority score as an integer.
        """
        score = 0
        if self.is_datum_candidate:
            score += 100
        if self.tolerance_grade in ("IT5", "IT6", "IT7"):
            score += 30
        if self.priority == "high":
            score += 20
        if self.geometric_type == "plane":
            score += 10
        if self.type == "end_face":
            score += 50
        if self.is_hole():
            score -= 20
        if self.type in ("groove", "chamfer", "slot"):
            score -= 30
        return score

    def to_dict(self) -> dict[str, Any]:
        """Convert the machining feature to a dictionary representation.

        Returns:
            A dictionary containing all feature properties suitable for serialization.
        """
        return {
            "name": self.name,
            "type": self.type,
            "geometric_type": self.geometric_type,
            "tolerance_grade": self.tolerance_grade,
            "surface_roughness_ra": self.surface_roughness_ra,
            "is_datum_candidate": self.is_datum_candidate,
            "machining_method": self.machining_method,
            "priority": self.priority,
            "surface": self.surface,
            "dimensions": self.dimensions,
            "parent_feature": self.parent_feature,
            "tolerances": self.tolerances,
        }


@dataclass
class Setup:
    """A machining setup representing one clamping configuration.

    Attributes:
        name: Setup name, e.g. '装夹1-面A'.
        surface: Surface identifier this setup works on.
        datum_features: List of datum feature names used in this setup.
        fixture_type: Fixture type description.
        clamped_features: List of feature names clamped in this setup.
    """

    name: str
    surface: str = "A"
    datum_features: list[str] = field(default_factory=list)
    fixture_type: str = ""
    clamped_features: list[str] = field(default_factory=list)


@dataclass
class FeatureEdge:
    """A directed dependency edge between two machining features.

    Attributes:
        from_feature: Predecessor feature name (must be machined first).
        to_feature: Successor feature name (machined after predecessor).
        relation: Dependency relation type, e.g. 'face_before_hole'.
    """

    from_feature: str
    to_feature: str
    relation: str


class FeatureDependencyGraph:
    """Directed graph representing manufacturing dependencies between features.

    Builds a dependency graph from machining features using manufacturing
    principles (face before hole, rough before finish, datum first), then
    produces an optimal machining sequence via topological sort.
    """

    def __init__(self) -> None:
        """Initialize an empty dependency graph."""
        self._features: dict[str, MachiningFeature] = {}
        self._edges: list[FeatureEdge] = []
        self._in_degree: dict[str, int] = {}
        self._adjacency: dict[str, list[str]] = {}

    def build_graph(self, features: list[MachiningFeature]) -> None:
        """Build the dependency graph from a list of machining features.

        Analyzes pairwise feature relationships and adds directed edges
        based on manufacturing principles.

        Args:
            features: List of machining features to build the graph from.
        """
        self._features = {f.name: f for f in features}
        self._edges = []
        self._adjacency = {f.name: [] for f in features}
        self._in_degree = {f.name: 0 for f in features}

        for fe in features:
            for other in features:
                if fe.name == other.name:
                    continue
                self._add_dependency_if_applicable(fe, other)

    def _add_dependency_if_applicable(
        self,
        pred: MachiningFeature,
        succ: MachiningFeature,
    ) -> None:
        """Add a dependency edge between two features if manufacturing rules apply.

        Rules checked (in order):
        1. Datum features before non-datum features
        2. Face before hole (end_face and plane before holes)
        3. Rough machining before finish machining (same geometric type)
        4. Higher priority features before lower priority (score diff > 40)
        5. Drilling before boring
        6. Parent feature before child feature

        Args:
            pred: Predecessor feature candidate.
            succ: Successor feature candidate.
        """
        if pred.is_datum_candidate and not succ.is_datum_candidate:
            self._add_edge(pred.name, succ.name, "datum_before_feature")
            return

        # 端面优先于孔——先面后孔
        if pred.type == "end_face" and succ.is_hole():
            self._add_edge(pred.name, succ.name, "face_before_hole")
            return

        # 平面优先于孔
        if pred.is_face() and succ.is_hole() and not pred.is_datum_candidate:
            self._add_edge(pred.name, succ.name, "face_before_hole")
            return

        # 粗加工优先于精加工
        if pred.is_rough() and succ.is_finish() and pred.geometric_type == succ.geometric_type:
            self._add_edge(pred.name, succ.name, "rough_before_finish")
            return

        # 高精度优先于低精度
        if pred.priority_score() > succ.priority_score() + 40:
            self._add_edge(pred.name, succ.name, "primary_before_secondary")
            return

        # 钻孔优先于镗孔
        if pred.type == "through_hole" and succ.type == "inner_bore":
            self._add_edge(pred.name, succ.name, "bore_after_drilling")
            return

        # 父特征优先于子特征
        if pred.name and succ.parent_feature and succ.parent_feature == pred.name:
            self._add_edge(pred.name, succ.name, "parent_child")

    def _add_edge(self, from_f: str, to_f: str, relation: str) -> None:
        """Add a directed dependency edge to the graph.

        Args:
            from_f: Predecessor feature name.
            to_f: Successor feature name.
            relation: Dependency relation type string.
        """
        if to_f not in self._adjacency.get(from_f, []):
            edge = FeatureEdge(from_feature=from_f, to_feature=to_f, relation=relation)
            self._edges.append(edge)
            self._adjacency.setdefault(from_f, []).append(to_f)
            self._in_degree[to_f] = self._in_degree.get(to_f, 0) + 1

    def get_machining_sequence(self) -> list[MachiningFeature]:
        """Get the optimal machining sequence via topological sort.

        Features with zero in-degree are sorted by priority score (highest first),
        ensuring that manufacturing rules are respected while maximizing
        machining efficiency.

        Returns:
            Ordered list of MachiningFeature objects in optimal machining sequence.
        """
        in_deg = dict(self._in_degree)
        queue = [name for name, deg in in_deg.items() if deg == 0]

        queue.sort(key=lambda n: -self._features[n].priority_score())

        result: list[str] = []
        while queue:
            current = queue.pop(0)
            result.append(current)
            for neighbor in self._adjacency.get(current, []):
                in_deg[neighbor] -= 1
                if in_deg[neighbor] == 0:
                    queue.append(neighbor)
            queue.sort(key=lambda n: -self._features[n].priority_score())

        for fname in self._features:
            if fname not in result:
                result.append(fname)

        return [self._features[name] for name in result]

    def check_accessibility(
        self,
        feature: MachiningFeature,
        current_setup: Setup,
    ) -> bool:
        """Check whether a feature is accessible in the current setup.

        A feature is inaccessible if it is clamped in the current setup
        or if it belongs to a different surface than the current setup.

        Args:
            feature: Feature to check accessibility for.
            current_setup: Current machining setup.

        Returns:
            True if the feature can be machined in the current setup.
        """
        if feature.name in current_setup.clamped_features:
            return False

        if feature.surface != current_setup.surface:
            return False

        return True

    def get_dependencies(self, feature_name: str) -> list[str]:
        """Get all features that the given feature depends on.

        Args:
            feature_name: Name of the feature to query.

        Returns:
            List of predecessor feature names.
        """
        deps: list[str] = []
        for edge in self._edges:
            if edge.to_feature == feature_name:
                deps.append(edge.from_feature)
        return deps

    def get_dependents(self, feature_name: str) -> list[str]:
        """Get all features that depend on the given feature.

        Args:
            feature_name: Name of the feature to query.

        Returns:
            List of successor feature names.
        """
        return list(self._adjacency.get(feature_name, []))

    @classmethod
    def from_features_list(
        cls,
        features: list[dict[str, Any]],
    ) -> FeatureDependencyGraph:
        """Create a dependency graph from a list of feature dictionaries.

        Args:
            features: List of feature dictionaries with standard keys
                (name, type, geometric_type, tolerance_grade, etc.).

        Returns:
            A fully built FeatureDependencyGraph instance.
        """
        mf_list = []
        for f in features:
            mf = MachiningFeature(
                name=f.get("name", ""),
                type=f.get("type", ""),
                geometric_type=f.get("geometric_type", ""),
                tolerance_grade=f.get("tolerance_grade", "IT8"),
                surface_roughness_ra=f.get("surface_roughness_ra", 6.3),
                is_datum_candidate=f.get("is_datum_candidate", False),
                machining_method=f.get("machining_method", ""),
                priority=f.get("priority", "medium"),
                surface=f.get("surface", "A"),
                dimensions=f.get("dimensions", {}),
                parent_feature=f.get("parent_feature", ""),
                tolerances=f.get("tolerances", {}),
            )
            mf_list.append(mf)
        graph = cls()
        graph.build_graph(mf_list)
        return graph
