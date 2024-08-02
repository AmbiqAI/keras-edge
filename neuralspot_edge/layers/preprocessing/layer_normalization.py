import keras
from .base_augmentation import BaseAugmentation1D, BaseAugmentation2D


class LayerNormalization1D(BaseAugmentation1D):
    def __init__(
        self,
        epsilon: float = 1e-6,
        name=None,
        **kwargs,
    ):
        """Apply Layer Normalization to the input.

        Args:
            epsilon (float): Small value to avoid division by zero.
        """
        super().__init__(name=name, **kwargs)
        self.epsilon = epsilon

    def augment_samples(self, inputs) -> keras.KerasTensor:
        """Augment a batch of samples during training."""

        samples = inputs[self.SAMPLES]

        mean = keras.ops.mean(samples, axis=self.data_axis, keepdims=True)
        variance = keras.ops.var(samples, axis=self.data_axis, keepdims=True)
        outputs = (samples - mean) / keras.ops.sqrt(variance + self.epsilon)

        return outputs  # (batch, duration, channels)


class LayerNormalization2D(BaseAugmentation2D):
    def __init__(
        self,
        epsilon: float = 1e-6,
        name=None,
        **kwargs,
    ):
        """Apply Layer Normalization to the input.

        Args:
            epsilon (float): Small value to avoid division by zero.
        """
        super().__init__(name=name, **kwargs)
        self.epsilon = epsilon

    def augment_samples(self, inputs) -> keras.KerasTensor:
        """Augment a batch of samples during training."""

        samples = inputs[self.SAMPLES]
        axis = (self.height_axis, self.width_axis)
        mean = keras.ops.mean(samples, axis=axis, keepdims=True)
        variance = keras.ops.var(samples, axis=axis, keepdims=True)
        outputs = (samples - mean) / keras.ops.sqrt(variance + self.epsilon)

        return outputs  # (batch, duration, channels)
