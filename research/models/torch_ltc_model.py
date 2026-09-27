"""LTC (Liquid Time-Constant) Model for Adaptive Temporal Pattern Modeling.

Implements learnable time constants tau for adaptive temporal dynamics.
Core update formula: h_new = h + dt * (dh - h) / tau

Key components:
    - LTCCell: Individual LTC cell with learnable time constants and weight matrices.
    - LTCModel: Full multi-layer LTC model inheriting from BaseLNN.

Example:
    >>> from models.torch_base_lnn import LNNConfig
    >>> config = LNNConfig(input_size=64, hidden_size=128, output_size=10)
    >>> model = LTCModel(config)
    >>> x = torch.randn(32, 64)
    >>> output, hidden = model(x, dt=0.1)
"""

import torch
import torch.nn as nn
from typing import Tuple

from .torch_base_lnn import BaseLNN, LNNConfig

# LTC model constants
MIN_TAU_VALUE = 0.1  # Minimum time constant to prevent division by zero


class LTCCell(nn.Module):
    """LTC cell with learnable time constants and weight matrices.

    Implements the liquid time-constant update mechanism:
    h_new = h + dt * (tanh(W @ x + U @ h + b) - h) / tau

    The time constant tau allows the network to adaptively respond to
    different temporal scales in the data.

    Attributes:
        input_size: Input feature dimension.
        hidden_size: Hidden layer dimension.
        W: Input-to-hidden weight matrix.
        U: Hidden-to-hidden weight matrix.
        bias: Bias vector.
        tau: Learnable time constant (clamped >= 0.1).

    Example:
        >>> cell = LTCCell(input_size=64, hidden_size=128)
        >>> x = torch.randn(32, 64)
        >>> h = torch.zeros(32, 128)
        >>> h_new = cell(x, h, dt=0.1)
    """

    def __init__(self, input_size: int, hidden_size: int):
        """
        Initialize LTCCell

        Args:
            input_size: Input feature dimension
            hidden_size: Hidden layer dimension
        """
        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size

        self.W = nn.Parameter(torch.empty(hidden_size, input_size, dtype=torch.float32))
        self.U = nn.Parameter(torch.empty(hidden_size, hidden_size, dtype=torch.float32))
        self.bias = nn.Parameter(torch.empty(hidden_size, dtype=torch.float32))

        self.tau = nn.Parameter(torch.ones(hidden_size))

        self._init_weights()

    def _init_weights(self) -> None:
        """Initialize weights using Xavier initialization"""
        nn.init.xavier_uniform_(self.W)
        nn.init.xavier_uniform_(self.U)
        nn.init.zeros_(self.bias)

    def forward(self, x: torch.Tensor, h: torch.Tensor, dt: float) -> torch.Tensor:
        """
        Core state update computation

        Args:
            x: Input tensor (batch_size, input_size)
            h: Hidden state tensor (batch_size, hidden_size)
            dt: Time step

        Returns:
            Updated hidden state tensor
        """
        tau = self.tau.clamp(min=MIN_TAU_VALUE)

        dh = torch.mm(h, self.U.t()) + torch.mm(x, self.W.t()) + self.bias
        dh = torch.tanh(dh)

        h_new = h + dt * (dh - h) / tau

        return h_new


class LTCModel(BaseLNN):
    """
    LTC model inheriting from BaseLNN.

    Features:
    - Learnable time constant tau
    - Adaptive to different time-scale patterns
    - Suitable for time series prediction tasks
    """

    def __init__(self, config: LNNConfig):
        """
        Initialize LTCModel

        Args:
            config: LNNConfig object containing model parameters
        """
        super().__init__(config)

        self.ltc_cells = nn.ModuleList(
            [
                LTCCell(
                    config.input_size if i == 0 else config.hidden_size,
                    config.hidden_size,
                )
                for i in range(config.num_layers)
            ]
        )

        self.output_layer = nn.Linear(config.hidden_size, config.output_size)

        if config.dropout > 0:
            self.dropout = nn.Dropout(config.dropout)
        else:
            self.dropout = nn.Identity()

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

    def forward(
        self,
        x: torch.Tensor,
        dt: float = None,
        hidden_state: torch.Tensor = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Forward propagation

        Args:
            x: Input tensor (batch_size, input_size) or (batch_size, seq_len, input_size)
            dt: Time step (float)
            hidden_state: Hidden state tensor (num_layers, batch_size, hidden_size)

        Returns:
            Tuple of (output tensor, updated hidden state tensor)
        """
        if dt is None:
            dt = self.config.time_constant

        batch_size = x.shape[0] if x.dim() == 2 else x.shape[0]

        if hidden_state is None:
            if self.hidden_state is None:
                hidden_state = self.init_hidden(batch_size)
            else:
                if self.hidden_state.shape[1] != batch_size:
                    hidden_state = self.init_hidden(batch_size)
                else:
                    hidden_state = self.hidden_state

        if x.dim() == 3:
            seq_len = x.shape[1]
            outputs = []
            for t in range(seq_len):
                x_t = x[:, t, :]
                hidden_state = self._apply_layers(x_t, hidden_state, dt)
                out = self.dropout(hidden_state[-1])
                out = self.output_layer(out)
                outputs.append(out)
            output = torch.stack(outputs, dim=1)
        else:
            hidden_state = self._apply_layers(x, hidden_state, dt)
            output = self.dropout(hidden_state[-1])
            output = self.output_layer(output)

        self.hidden_state = hidden_state
        return output, hidden_state

    def _apply_layers(
        self,
        x: torch.Tensor,
        hidden_state: torch.Tensor,
        dt: float,
    ) -> torch.Tensor:
        """Apply multiple LTC layers"""
        new_hidden = []
        layer_input = x
        for i, cell in enumerate(self.ltc_cells):
            h = hidden_state[i]
            h_new = cell(layer_input, h, dt)
            new_hidden.append(h_new)
            layer_input = h_new
        return torch.stack(new_hidden, dim=0)

    def init_hidden(self, batch_size: int) -> torch.Tensor:
        """
        Initialize hidden state with zeros

        Args:
            batch_size: Batch size

        Returns:
            Zero-initialized hidden state tensor (num_layers, batch_size, hidden_size)
        """
        return torch.zeros(
            self.config.num_layers,
            batch_size,
            self.config.hidden_size,
            device=self.device,
        )

    def reset(self) -> None:
        """Reset hidden state"""
        self.hidden_state = None
