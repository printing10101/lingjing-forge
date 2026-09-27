"""512-Dimensional Unified Manufacturing Semantic Embedding Space.

Space structure:
  [0:64)    Material    - material properties (hardness, conductivity, ductility)
  [64:192)  Process     - machining methods (turning, milling, drilling, grinding)
  [192:224) Precision   - dimensional tolerance grades (IT5-IT12)
  [224:352) State       - equipment/tool real-time status (vibration, temp, wear)
  [352:384) Risk        - safety risk levels (probability and severity)
  [384:512) Reserved    - future extensions and system optimization

本模块为门面：实现已拆分至 _axes / _space / _holder。
"""

from __future__ import annotations

from app.ai.unified_embedding._axes import (  # noqa: F401
    MATERIAL_DIMS,
    MATERIAL_OFFSET,
    PRECISION_DIMS,
    PRECISION_OFFSET,
    PROCESS_DIMS,
    PROCESS_OFFSET,
    RESERVED_DIMS,
    RESERVED_OFFSET,
    RISK_DIMS,
    RISK_OFFSET,
    STATE_DIMS,
    STATE_OFFSET,
    TOTAL_DIMS,
    MaterialAxis,
    PrecisionAxis,
    ProcessAxis,
    RiskAxis,
    SemanticAxis,
    StateAxis,
)
from app.ai.unified_embedding._holder import get_embedding_space  # noqa: F401
from app.ai.unified_embedding._space import EmbeddingSpace  # noqa: F401
