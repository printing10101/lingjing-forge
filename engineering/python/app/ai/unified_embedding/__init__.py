"""Unified Manufacturing Semantic Embedding Space.

LLM + LNN + JEPA hybrid architecture for cross-modal, cross-layer
knowledge sharing in the Lingjing Manufacturing system.

The 512-dimensional embedding space is organized into six semantic axes:
    - Material (64 dims): material properties
    - Process (128 dims): machining methods and operations
    - Precision (32 dims): dimensional tolerance grades
    - State (128 dims): equipment/tool real-time status
    - Risk (32 dims): safety risk assessment
    - Reserved (128 dims): future extensions
"""

from app.ai.unified_embedding.space import (
    EmbeddingSpace,
    SemanticAxis,
    MaterialAxis,
    ProcessAxis,
    PrecisionAxis,
    StateAxis,
    RiskAxis,
    get_embedding_space,
)

from app.ai.unified_embedding.encoder import (
    EmbeddingEncoder,
    LLMProjector,
    LNNProjector,
    JEPAProjector,
    MultiModalEncoder,
)

from app.ai.unified_embedding.aligner import (
    EmbeddingAligner,
    ContrastiveAligner,
    TripletLossConfig,
    AlignerConfig,
)

from app.ai.unified_embedding.retriever import (
    CrossLayerRetriever,
    RetrievalResult,
    BatchRetrievalResult,
)

from app.ai.unified_embedding.interfaces import (
    CognitiveToPerceptionRequest,
    CognitiveToPerceptionResponse,
    PerceptionToExecutionRequest,
    PerceptionToExecutionResponse,
    ExecutionToCognitiveRequest,
    ExecutionToCognitiveResponse,
    MachiningProcessFlow,
    QualityRequirements,
    DimensionalTolerance,
    QualityLevel,
    CuttingParameters,
    RealTimeState,
)

__all__ = [
    "EmbeddingSpace",
    "SemanticAxis",
    "MaterialAxis",
    "ProcessAxis",
    "PrecisionAxis",
    "StateAxis",
    "RiskAxis",
    "get_embedding_space",
    "EmbeddingEncoder",
    "LLMProjector",
    "LNNProjector",
    "JEPAProjector",
    "MultiModalEncoder",
    "EmbeddingAligner",
    "ContrastiveAligner",
    "TripletLossConfig",
    "AlignerConfig",
    "CrossLayerRetriever",
    "RetrievalResult",
    "BatchRetrievalResult",
    "CognitiveToPerceptionRequest",
    "CognitiveToPerceptionResponse",
    "PerceptionToExecutionRequest",
    "PerceptionToExecutionResponse",
    "ExecutionToCognitiveRequest",
    "ExecutionToCognitiveResponse",
    "MachiningProcessFlow",
    "QualityRequirements",
    "DimensionalTolerance",
    "QualityLevel",
    "CuttingParameters",
    "RealTimeState",
]
