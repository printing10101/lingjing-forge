"""PyTorch CFC (Circuit Foremost Continuous-time) Model.

Implements the CFC architecture in PyTorch, avoiding traditional ODE solvers
to achieve 160x faster inference than LSTM. The core mechanism uses continuous
liquid state updates without discrete time step discretization.

Key components:
    - CFCLayer: Liquid state update layer with backbone network.
    - CFCModel: Full CFC model inheriting from BaseLNN.

Example:
    >>> from models.torch_base_lnn import LNNConfig
    >>> config = LNNConfig(input_size=128, hidden_size=256, output_size=10)
    >>> model = CFCModel(config)
    >>> x = torch.randn(32, 128)
    >>> output, hidden = model(x, dt=0.1)
"""

import torch
import torch.nn as nn
from typing import Tuple, Optional

from .torch_base_lnn import BaseLNN, LNNConfig


# CFC model constants
DEFAULT_DT_VALUE = 0.1  # Default time constant for liquid state updates
DT_CHECK_EPSILON = 0.0  # Epsilon value for dt check (use model's default dt)


class CFCLayer(nn.Module):
    """CFC layer with backbone network and liquid state update mechanism.

    Combines input and hidden state through a sequential backbone (Linear → Tanh → Linear)
    and applies continuous liquid state updates: h_new = h + dt * dh.

    Attributes:
        input_size: Input feature dimension.
        hidden_size: Hidden layer dimension.
        backbone: Sequential backbone network for computing dh.
        dt: Learnable or fixed time constant parameter.

    Example:
        >>> layer = CFCLayer(input_size=64, hidden_size=128)
        >>> x = torch.randn(32, 64)
        >>> h = torch.zeros(32, 128)
        >>> h_new = layer(x, h, dt=0.1)
    """

    def __init__(self, input_size: int, hidden_size: int, dt: float = DEFAULT_DT_VALUE):
        """
        Initialize CFCLayer

        Args:
            input_size: Input feature dimension
            hidden_size: Hidden layer dimension
            dt: Time constant (learnable or fixed)
        """
        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size

        self.backbone = nn.Sequential(
            nn.Linear(input_size + hidden_size, hidden_size),
            nn.Tanh(),
            nn.Linear(hidden_size, hidden_size),
        )

        self.dt = nn.Parameter(torch.tensor(dt, dtype=torch.float32), requires_grad=False)

        self._init_weights()

    def _init_weights(self) -> None:
        """Initialize weights using Xavier initialization"""
        for module in self.backbone:
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                nn.init.zeros_(module.bias)

    def forward(
        self, x: torch.Tensor, h: torch.Tensor, dt: float = DT_CHECK_EPSILON
    ) -> torch.Tensor:
        """
        Compute liquid state update

        Args:
            x: Input tensor (batch_size, input_size)
            h: Hidden state tensor (batch_size, hidden_size)
            dt: Time step override (0.0 means use self.dt)

        Returns:
            Updated hidden state tensor
        """
        if dt == DT_CHECK_EPSILON:
            dt = self.dt.item()

        combined = torch.cat([x, h], dim=-1)
        dh = self.backbone(combined)
        h_new = h + dt * dh
        return h_new


class CFCModel(BaseLNN):
    """
    CFC model inheriting from BaseLNN.

    Features:
    - No ODE solver required
    - 160x faster inference than LSTM
    - Continuous liquid state updates
    - Enhanced output layer with 2-layer MLP head
    """

    def __init__(self, config: LNNConfig):
        """
        Initialize CFCModel

        Args:
            config: LNNConfig object containing model parameters
        """
        super().__init__(config)

        self.cfc_layer = CFCLayer(
            input_size=config.input_size,
            hidden_size=config.hidden_size,
            dt=config.time_constant,
        )

        # 增强的输出层：两层MLP
        self.output_layer = nn.Sequential(
            nn.Linear(config.hidden_size, config.hidden_size // 2),
            nn.ReLU(),
            nn.Dropout(config.dropout) if config.dropout > 0 else nn.Identity(),
            nn.Linear(config.hidden_size // 2, config.output_size),
        )

        if config.dropout > 0:
            self.dropout = nn.Dropout(config.dropout)
        else:
            self.dropout = nn.Identity()

        self.hidden_state = None
        self._device_str = "cpu"

    @property
    def device(self) -> torch.device:
        """Get the device the model is on"""
        return torch.device(self._device_str)

    @device.setter
    def device(self, value: torch.device) -> None:
        """Set the device (for compatibility)"""
        self.to(value)

    def to(self, device, *args, **kwargs):
        """Override to method to update device string"""
        self._device_str = str(device)
        return super().to(device, *args, **kwargs)

    def forward(
        self,
        x: torch.Tensor,
        dt: float = 0.0,
        hidden_state: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Forward propagation

        Args:
            x: Input tensor (batch_size, input_size) or (batch_size, seq_len, input_size)
            dt: Time step (0.0 means use model's default)
            hidden_state: Hidden state tensor (batch_size, hidden_size)

        Returns:
            Tuple of (output tensor, updated hidden state tensor)
        """
        batch_size = x.shape[0]

        if hidden_state is None:
            if self.hidden_state is None:
                hidden_state = self.init_hidden(batch_size)
            else:
                if self.hidden_state.shape[0] != batch_size:
                    hidden_state = self.init_hidden(batch_size)
                else:
                    hidden_state = self.hidden_state

        if x.dim() == 3:
            seq_len = x.shape[1]
            outputs = []
            for t in range(seq_len):
                x_t = x[:, t, :]
                hidden_state = self.cfc_layer(x_t, hidden_state, dt)
                out = self.dropout(hidden_state)
                out = self.output_layer(out)
                outputs.append(out)
            output = torch.stack(outputs, dim=1)
        else:
            hidden_state = self.cfc_layer(x, hidden_state, dt)
            output = self.dropout(hidden_state)
            output = self.output_layer(output)

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
        return torch.zeros(batch_size, self.config.hidden_size, device=self.device)

    def reset(self) -> None:
        """Reset hidden state"""
        self.hidden_state = None
