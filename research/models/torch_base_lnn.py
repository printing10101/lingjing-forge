"""PyTorch-based BaseLNN Abstract Class with Hidden State Management.

Defines the unified interface for all PyTorch-based LNN models. This module
provides an abstract base class that inherits from both torch.nn.Module and
ABC, enabling PyTorch functionality (autograd, JIT tracing) alongside
abstract method enforcement for consistent model contracts.

Key components:
    - LNNConfig: Configuration class for model hyperparameters.
    - BaseLNN: Abstract base class with hidden state management and TorchScript export.

Example:
    >>> config = LNNConfig(input_size=10, hidden_size=64, output_size=2)
    >>> class MyModel(BaseLNN):
    ...     def forward(self, x, dt, hidden_state): return x, hidden_state
    ...     def init_hidden(self, batch_size): return torch.zeros(batch_size, 64)
"""

import torch
import torch.nn as nn
from abc import ABC, ABCMeta, abstractmethod
from typing import Any, Dict, Tuple


# Base LNN model constants
DEFAULT_TORCHSCRIPT_DT = 0.1  # Default dt for TorchScript tracing


# 解决 nn.Module 和 ABC 多重继承的元类冲突
class _ModuleABCMeta(ABCMeta, type(nn.Module)):
    """元类：协调 nn.Module 和 ABC 的元类"""
    pass


class LNNConfig:
    """Configuration class for LNN model hyperparameters.

    Encapsulates all architectural parameters needed to instantiate an LNN model,
    including layer dimensions, regularization, and temporal dynamics.

    Attributes:
        input_size: Input feature dimension.
        hidden_size: Hidden layer dimension.
        output_size: Output prediction dimension.
        num_layers: Number of recurrent/LNN layers.
        dropout: Dropout ratio for regularization (0.0-1.0).
        time_constant: Time constant for liquid state updates (dt in ODE).

    Example:
        >>> config = LNNConfig(input_size=128, hidden_size=256, output_size=10)
        >>> config.to_dict()["hidden_size"]
        256
    """

    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        output_size: int,
        num_layers: int = 1,
        dropout: float = 0.0,
        time_constant: float = 1.0,
    ):
        """
        Initialize LNN configuration

        Args:
            input_size: Input feature dimension
            hidden_size: Hidden layer dimension
            output_size: Output dimension
            num_layers: Number of network layers
            dropout: Dropout ratio (0-1)
            time_constant: Time constant for liquid state updates
        """
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.num_layers = num_layers
        self.dropout = dropout
        self.time_constant = time_constant

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary"""
        return {
            "input_size": self.input_size,
            "hidden_size": self.hidden_size,
            "output_size": self.output_size,
            "num_layers": self.num_layers,
            "dropout": self.dropout,
            "time_constant": self.time_constant,
        }

    def __repr__(self) -> str:
        return f"LNNConfig({self.to_dict()})"


class BaseLNN(nn.Module, ABC, metaclass=_ModuleABCMeta):
    """
    Abstract base class for all LNN models.

    Inherits from both torch.nn.Module and ABC to provide
    both PyTorch functionality and abstract method enforcement.
    """

    def __init__(self, config: LNNConfig):
        super().__init__()
        self.config = config
        self.input_dim = config.input_size
        self.output_dim = config.output_size
        self.is_trained = False
        self.model_name = "BaseLNN"
        # 使用默认 CPU 设备，避免在初始化早期调用 parameters()
        self._device = torch.device("cpu")

    @abstractmethod
    def forward(
        self,
        x: torch.Tensor,
        dt: float,
        hidden_state: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Forward propagation

        Args:
            x: Input tensor of shape (batch_size, input_size)
            dt: Time step (float)
            hidden_state: Hidden state tensor of shape (batch_size, hidden_size)

        Returns:
            Tuple of (output tensor, updated hidden state tensor)
        """
        pass

    @abstractmethod
    def init_hidden(self, batch_size: int) -> torch.Tensor:
        """
        Initialize hidden state

        Args:
            batch_size: Batch size

        Returns:
            Initialized hidden state tensor
        """
        pass

    def to_torchscript(self, example_input: torch.Tensor) -> torch.jit.ScriptModule:
        """
        Export model to TorchScript format

        Args:
            example_input: Example input tensor for type inference

        Returns:
            TorchScript model object
        """
        self.eval()
        hidden = self.init_hidden(example_input.shape[0])
        dt = DEFAULT_TORCHSCRIPT_DT
        return torch.jit.trace(self, (example_input, torch.tensor(dt), hidden))

    def get_info(self) -> Dict[str, Any]:
        """
        Get model information

        Returns:
            Dictionary containing parameter count, device info, and config parameters
        """
        total_params = sum(p.numel() for p in self.parameters())
        trainable_params = sum(p.numel() for p in self.parameters() if p.requires_grad)

        return {
            "total_parameters": total_params,
            "trainable_parameters": trainable_params,
            "device": str(self.device),
            "config": self.config.to_dict(),
        }

    def reset(self) -> None:
        """
        Reset model state including hidden states and internal variables.
        Subclasses should override this method to reset their specific states.
        """
        pass

    @property
    def device(self) -> torch.device:
        """Get the device the model is on"""
        return self._device

    @device.setter
    def device(self, value: torch.device) -> None:
        """Set the device"""
        self._device = value

    def __repr__(self) -> str:
        info = self.get_info()
        return f"{self.__class__.__name__}(params={info['total_parameters']}, device={info['device']})"
