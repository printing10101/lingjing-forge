"""Bayesian LNN Model with MC Dropout for Uncertainty Quantification.

Wraps an existing LNN model (e.g. CFCModel) and adds MC Dropout layers
to enable Monte Carlo Dropout inference for uncertainty estimation.

The key idea: during inference, keep dropout layers active and run
multiple forward passes. The variance across samples estimates
predictive uncertainty.
"""

import torch
import torch.nn as nn
from typing import Tuple, Optional

from .torch_base_lnn import BaseLNN, LNNConfig
from .torch_cfc_model import CFCModel


class BayesianLNN(BaseLNN):
    """Bayesian approximation of LNN via MC Dropout.

    Wraps a pre-trained CFCModel, injects additional dropout layers into
    the computation graph, and provides MC Dropout inference to produce
    both mean predictions and uncertainty estimates (std).

    Args:
        config: LNNConfig for the underlying model architecture.
        dropout_prob: Dropout probability for MC Dropout layers. Default 0.1.
        base_model: Optional pre-built base model. If None, a CFCModel is created.

    Example:
        >>> config = LNNConfig(input_size=8, hidden_size=64, output_size=4)
        >>> model = BayesianLNN(config, dropout_prob=0.1)
        >>> mean, std = model.predict_with_uncertainty(x, n_samples=50)
    """

    def __init__(
        self,
        config: LNNConfig,
        dropout_prob: float = 0.1,
        base_model: Optional[nn.Module] = None,
    ):
        super().__init__(config)
        self.model_name = "BayesianLNN"
        self.dropout_prob = dropout_prob

        # Build or accept the base model
        if base_model is not None:
            self.base_model = base_model
        else:
            self.base_model = CFCModel(config)

        # Inject MC Dropout layers after the backbone's first Linear and before output
        self.mc_dropout = nn.Dropout(p=dropout_prob)

        # Ensure base model has dropout enabled for MC sampling
        self._ensure_dropout_layers()

        self._device_str = "cpu"

    def _ensure_dropout_layers(self) -> None:
        """Ensure the base model has dropout layers for MC sampling.

        If the base model's dropout is Identity (dropout=0 in config),
        replace it with a real Dropout layer so MC sampling works.
        """
        if hasattr(self.base_model, "dropout"):
            existing = self.base_model.dropout
            if isinstance(existing, nn.Identity):
                self.base_model.dropout = nn.Dropout(p=self.dropout_prob)

        # Also inject dropout into the output_layer if possible
        if hasattr(self.base_model, "output_layer"):
            ol = self.base_model.output_layer
            if isinstance(ol, nn.Sequential):
                has_dropout = any(isinstance(m, nn.Dropout) for m in ol)
                if not has_dropout:
                    # Rebuild output_layer with dropout inserted
                    new_layers = []
                    for mod in ol:
                        new_layers.append(mod)
                        if isinstance(mod, nn.ReLU):
                            new_layers.append(nn.Dropout(p=self.dropout_prob))
                    self.base_model.output_layer = nn.Sequential(*new_layers)

    @property
    def device(self) -> torch.device:
        return torch.device(self._device_str)

    @device.setter
    def device(self, value: torch.device) -> None:
        # 仅更新设备字符串，不触发 to() 避免无限递归
        self._device_str = str(value)

    def to(self, device, *args, **kwargs):
        self._device_str = str(device)
        # 直接调用 nn.Module.to() 而不是 super().to() 避免触发 device setter
        return nn.Module.to(self, device, *args, **kwargs)

    def forward(
        self,
        x: torch.Tensor,
        dt: float = 0.0,
        hidden_state: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """Single forward pass (one MC sample).

        Args:
            x: Input tensor (batch_size, input_size).
            dt: Time step.
            hidden_state: Optional hidden state.

        Returns:
            Tuple of (output, hidden_state).
        """
        return self.base_model(x, dt=dt, hidden_state=hidden_state)

    def init_hidden(self, batch_size: int) -> torch.Tensor:
        """Delegate hidden state init to base model."""
        return self.base_model.init_hidden(batch_size)

    def reset(self) -> None:
        """Reset base model state."""
        self.base_model.reset()

    def predict_with_uncertainty(
        self,
        x: torch.Tensor,
        n_samples: int = 50,
        dt: float = 0.0,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """MC Dropout inference returning mean and std.

        Keeps dropout active during inference, runs n_samples forward
        passes, and returns the mean and standard deviation of outputs.

        Args:
            x: Input tensor (batch_size, input_size).
            n_samples: Number of MC samples. Default 50.
            dt: Time step for the LNN forward pass.

        Returns:
            Tuple of (mean, std) tensors, each (batch_size, output_size).
        """
        # Save original training mode and restore after MC sampling
        was_training = self.training

        # Keep dropout active (train mode for dropout layers)
        self.train()

        batch_size = x.shape[0]

        # Batch all MC samples into a single forward pass for efficiency
        # Repeat input n_samples times: (batch_size, input_size) -> (n_samples * batch_size, input_size)
        x_batched = x.repeat(n_samples, 1)

        # Reset hidden state once before the batched forward pass
        self.base_model.reset()

        # Single forward pass with all samples
        with torch.no_grad():
            out_batched, _ = self.forward(x_batched, dt=dt)

        # Reshape output: (n_samples * batch_size, output_size) -> (n_samples, batch_size, output_size)
        output_size = out_batched.shape[-1]
        outputs = out_batched.view(n_samples, batch_size, output_size)

        # Compute mean and std across samples
        mean = outputs.mean(dim=0)
        std = outputs.std(dim=0)

        # Restore original training mode
        if not was_training:
            self.eval()

        return mean, std

    def load_base_weights(self, state_dict: dict, strict: bool = False) -> None:
        """Load pre-trained LNN weights into the base model.

        Args:
            state_dict: State dict from a trained LNN model.
            strict: Whether to strictly match keys. Default False to
                    allow loading from models without injected dropout.
        """
        self.base_model.load_state_dict(state_dict, strict=strict)
