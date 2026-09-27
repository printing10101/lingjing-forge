"""Hybrid LNN Model (CNN + LTC).

Combines CNN feature extraction with LTC temporal modeling for tasks that
require both spatial and temporal pattern recognition.

Architecture:
    1. CNN layers: Extract local spatial features from input data.
    2. LTC layers: Model temporal dependencies of extracted features.
    3. Fully connected layers: Final output predictions.

Key components:
    - HybridLNN: Full hybrid model inheriting from BaseLNN.

Example:
    >>> from models.torch_base_lnn import LNNConfig
    >>> config = LNNConfig(input_size=128, hidden_size=256, output_size=10)
    >>> model = HybridLNN(config)
    >>> x = torch.randn(32, 128)
    >>> output, hidden = model(x, dt=0.1)
"""

import torch
import torch.nn as nn
from typing import Tuple

from .torch_base_lnn import BaseLNN, LNNConfig
from .torch_ltc_model import LTCCell


# Hybrid LNN model constants
DEFAULT_CNN_KERNEL_SIZES = [5, 5, 5, 3, 3]  # Default kernel sizes for CNN layers
DEFAULT_OUTPUT_DROPOUT_RATE = 0.2  # Default dropout rate for output layer


class HybridLNN(BaseLNN):
    """Hybrid LNN model combining CNN feature extraction with LTC temporal modeling.

    Inherits from BaseLNN and conforms to unified interface specifications.
    Processes input through a multi-layer CNN (with BatchNorm and ReLU) to extract
    spatial features, then through LTC cells for temporal modeling, and finally
    through fully connected layers for output prediction.

    Attributes:
        config: LNNConfig for overall model (input_size, hidden_size for CNN, output_size).
        ltc_config: LNNConfig for LTC layer settings.
        cnn: CNN feature extractor sequential network.
        ltc_cells: ModuleList of LTCCell layers.
        output_layer: Output fully connected layers.
        hidden_state: Current hidden state tensor.
        device: Computation device.

    Example:
        >>> from models.torch_base_lnn import LNNConfig
        >>> config = LNNConfig(input_size=10, hidden_size=64, output_size=2)
        >>> model = HybridLNN(config)
        >>> x = torch.randn(32, 10)
        >>> output, hidden = model(x, dt=0.1)
    """

    def __init__(self, config: LNNConfig, ltc_config: LNNConfig = None):
        """
        Initialize HybridLNN

        Args:
            config: LNNConfig for overall model (input_size, hidden_size for CNN, output_size)
            ltc_config: LNNConfig for LTC layer (optional, will be auto-created if None)
        """
        super().__init__(config)

        self.cnn = self._build_cnn(
            input_channels=config.input_size,
            hidden_size=config.hidden_size,
            num_layers=config.num_layers,
        )

        cnn_output_dim = self._compute_cnn_output_dim(config.input_size)

        if ltc_config is None:
            ltc_config = LNNConfig(
                input_size=cnn_output_dim,
                hidden_size=config.hidden_size,
                output_size=config.output_size,
                num_layers=1,
                dropout=config.dropout,
                time_constant=config.time_constant,
            )

        self.ltc_cells = nn.ModuleList(
            [
                LTCCell(
                    ltc_config.input_size if i == 0 else ltc_config.hidden_size,
                    ltc_config.hidden_size,
                )
                for i in range(ltc_config.num_layers)
            ]
        )

        self.output_layer = self._build_output_layer(
            input_dim=ltc_config.hidden_size,
            hidden_dim=config.hidden_size // 2,
            output_size=config.output_size,
            use_dropout=config.dropout > 0,
            dropout_rate=config.dropout,
        )

        self.ltc_config = ltc_config
        self.hidden_state = None
        self.device = torch.device("cpu")

    @property
    def device(self) -> torch.device:
        """Get the device the model is on"""
        return next(self.parameters()).device

    @device.setter
    def device(self, value: torch.device) -> None:
        """Set the device (for compatibility)"""
        self.to(value)

    def _build_cnn(
        self, input_channels: int, hidden_size: int, num_layers: int = 3
    ) -> nn.Sequential:
        """
        Build CNN feature extractor with at least 3 Conv1d layers.

        Each conv layer is followed by BatchNorm1d and ReLU.
        MaxPool1d (stride 2) is added between conv layers only when
        input dimension is large enough to avoid zero-size output.
        """
        kernel_sizes = DEFAULT_CNN_KERNEL_SIZES[: max(num_layers, 3)]
        filter_sizes = [hidden_size // 4, hidden_size // 2, hidden_size] + [
            hidden_size
        ] * (max(num_layers, 3) - 3)

        layers = []
        in_channels = input_channels

        for i, (kernel_size, out_channels) in enumerate(
            zip(kernel_sizes, filter_sizes)
        ):
            layers.append(
                nn.Conv1d(
                    in_channels, out_channels, kernel_size, padding=kernel_size // 2
                )
            )
            layers.append(nn.BatchNorm1d(out_channels))
            layers.append(nn.ReLU())

            in_channels = out_channels

        layers.append(nn.AdaptiveAvgPool1d(1))

        return nn.Sequential(*layers)

    def _compute_cnn_output_dim(self, input_channels: int) -> int:
        """Compute CNN output feature dimension"""
        return (
            self.cnn[-1].out_channels
            if hasattr(self.cnn[-1], "out_channels")
            else self.config.hidden_size
        )

    def _build_output_layer(
        self,
        input_dim: int,
        hidden_dim: int,
        output_size: int,
        use_dropout: bool = True,
        dropout_rate: float = DEFAULT_OUTPUT_DROPOUT_RATE,
    ) -> nn.Sequential:
        """
        Build output fully connected layers with at least 2 layers.
        """
        layers = [
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
        ]

        if use_dropout:
            layers.append(nn.Dropout(dropout_rate))

        layers.append(nn.Linear(hidden_dim, output_size))

        return nn.Sequential(*layers)

    def forward(
        self,
        x: torch.Tensor,
        dt: float = None,
        hidden_state: torch.Tensor = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Forward propagation

        Flow:
        1. Input data passes through CNN feature extractor to get feature sequence
        2. Feature sequence inputs to LTC layer for temporal modeling
        3. LTC output passes through fully connected layers for final predictions
        4. Hidden state is correctly passed and maintained

        Args:
            x: Input tensor (batch_size, seq_len, input_size) or (batch_size, input_size)
            dt: Time step (float)
            hidden_state: Hidden state tensor (num_layers, batch_size, hidden_size)

        Returns:
            Tuple of (output tensor, updated hidden state tensor)
        """
        if dt is None:
            dt = self.config.time_constant

        batch_size = x.shape[0]

        if hidden_state is None:
            if self.hidden_state is None:
                hidden_state = self.init_hidden(batch_size)
            else:
                if self.hidden_state.shape[1] != batch_size:
                    hidden_state = self.init_hidden(batch_size)
                else:
                    hidden_state = self.hidden_state

        if x.dim() == 2:
            x = x.unsqueeze(1)

        batch_size, seq_len, input_size = x.shape

        x_cnn = x.transpose(1, 2)
        cnn_features = self.cnn(x_cnn)
        cnn_features = cnn_features.squeeze(-1)

        cnn_features = cnn_features.unsqueeze(1).expand(-1, seq_len, -1)

        ltc_outputs = []
        for t in range(seq_len):
            ltc_input = cnn_features[:, t, :]
            new_hidden = []
            layer_input = ltc_input
            for i, cell in enumerate(self.ltc_cells):
                h = hidden_state[i]
                h_new = cell(layer_input, h, dt)
                new_hidden.append(h_new)
                layer_input = h_new
            hidden_state = torch.stack(new_hidden, dim=0)

            ltc_output = hidden_state[-1]
            ltc_outputs.append(ltc_output)

        ltc_sequence = torch.stack(ltc_outputs, dim=1)

        pooled = ltc_sequence.mean(dim=1)
        output = self.output_layer(pooled)

        self.hidden_state = hidden_state
        return output, hidden_state

    def init_hidden(self, batch_size: int) -> torch.Tensor:
        """
        Initialize hidden state with zeros

        Args:
            batch_size: Batch size

        Returns:
            Zero-initialized hidden state tensor
        """
        return torch.zeros(
            self.ltc_config.num_layers,
            batch_size,
            self.ltc_config.hidden_size,
            device=self.device,
        )

    def reset(self) -> None:
        """Reset hidden state"""
        self.hidden_state = None
