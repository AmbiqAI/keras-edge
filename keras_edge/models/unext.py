"""UNext"""

from typing import Literal

import keras
from pydantic import BaseModel, Field


class UNextBlockParams(BaseModel):
    """UNext block parameters"""

    filters: int = Field(..., description="# filters")
    depth: int = Field(default=1, description="Layer depth")
    ddepth: int | None = Field(default=None, description="Layer decoder depth")
    kernel: int | tuple[int, int] = Field(default=3, description="Kernel size")
    pool: int | tuple[int, int] = Field(default=2, description="Pool size")
    strides: int | tuple[int, int] = Field(default=2, description="Stride size")
    skip: bool = Field(default=True, description="Add skip connection")
    expand_ratio: float = Field(default=1, description="Expansion ratio")
    se_ratio: float = Field(default=0, description="Squeeze and excite ratio")
    dropout: float | None = Field(default=None, description="Dropout rate")
    norm: Literal["batch", "layer"] | None = Field(
        default="layer", description="Normalization type"
    )


class UNextParams(BaseModel):
    """UNext parameters"""

    blocks: list[UNextBlockParams] = Field(
        default_factory=list, description="UNext blocks"
    )
    include_top: bool = Field(default=True, description="Include top")
    use_logits: bool = Field(default=True, description="Use logits")
    name: str = Field(default="UNext", description="Model name")
    output_kernel_size: int | tuple[int, int] = Field(
        default=3, description="Output kernel size"
    )
    output_kernel_stride: int | tuple[int, int] = Field(
        default=1, description="Output kernel stride"
    )


def se_block(ratio: int = 8, name: str | None = None):
    """Squeeze and excite block"""

    def layer(x: keras.KerasTensor) -> keras.KerasTensor:
        num_chan = x.shape[-1]
        # Squeeze
        y = keras.layers.GlobalAveragePooling2D(
            name=f"{name}.pool" if name else None, keepdims=True
        )(x)

        y = keras.layers.Conv2D(
            num_chan // ratio,
            kernel_size=1,
            use_bias=True,
            name=f"{name}.sq" if name else None,
        )(y)

        y = keras.layers.Activation("relu6", name=f"{name}.relu" if name else None)(y)

        # Excite
        y = keras.layers.Conv2D(
            num_chan, kernel_size=1, use_bias=True, name=f"{name}.ex" if name else None
        )(y)
        y = keras.layers.Activation(
            keras.activations.hard_sigmoid, name=f"{name}.sigg" if name else None
        )(y)
        y = keras.layers.Multiply(name=f"{name}.mul" if name else None)([x, y])
        return y

    return layer


def UNext_block(
    output_filters: int,
    expand_ratio: float = 1,
    kernel_size: int | tuple[int, int] = 3,
    strides: int | tuple[int, int] = 1,
    se_ratio: float = 4,
    dropout: float | None = 0,
    norm: Literal["batch", "layer"] | None = "batch",
    name: str | None = None,
) -> keras.Layer:
    """Create UNext block"""

    def layer(x: keras.KerasTensor) -> keras.KerasTensor:
        input_filters: int = x.shape[-1]
        strides_len = (
            strides if isinstance(strides, int) else sum(strides) // len(strides)
        )
        add_residual = input_filters == output_filters and strides_len == 1
        ln_axis = 2 if x.shape[1] == 1 else 1 if x.shape[2] == 1 else (1, 2)

        # Depthwise conv
        y = keras.layers.Conv2D(
            input_filters,
            kernel_size=kernel_size,
            groups=input_filters,
            strides=1,
            padding="same",
            use_bias=norm is None,
            kernel_initializer="he_normal",
            kernel_regularizer=keras.regularizers.L2(1e-3),
            name=f"{name}.dwconv" if name else None,
        )(x)
        if norm == "batch":
            y = keras.layers.BatchNormalization(
                name=f"{name}.norm",
            )(y)
        elif norm == "layer":
            y = keras.layers.LayerNormalization(
                axis=ln_axis,
                name=f"{name}.norm" if name else None,
            )(y)
        # END IF

        # Inverted expansion block
        y = keras.layers.Conv2D(
            filters=int(expand_ratio * input_filters),
            kernel_size=1,
            strides=1,
            padding="same",
            use_bias=norm is None,
            groups=input_filters,
            kernel_initializer="he_normal",
            kernel_regularizer=keras.regularizers.L2(1e-3),
            name=f"{name}.expand" if name else None,
        )(y)

        y = keras.layers.Activation(
            "relu6",
            name=f"{name}.relu" if name else None,
        )(y)

        # Squeeze and excite
        if se_ratio > 1:
            name_se = f"{name}.se" if name else None
            y = se_block(ratio=se_ratio, name=name_se)(y)

        y = keras.layers.Conv2D(
            filters=output_filters,
            kernel_size=1,
            strides=1,
            padding="same",
            use_bias=norm is None,
            kernel_initializer="he_normal",
            kernel_regularizer=keras.regularizers.L2(1e-3),
            name=f"{name}.project" if name else None,
        )(y)

        if add_residual:
            if dropout and dropout > 0:
                y = keras.layers.Dropout(
                    dropout,
                    noise_shape=(y.shape),
                    name=f"{name}.drop" if name else None,
                )(y)
            y = keras.layers.Add(name=f"{name}.res" if name else None)([x, y])
        return y

    # END DEF
    return layer


def unext_core(
    x: keras.KerasTensor,
    params: UNextParams,
) -> keras.KerasTensor:
    """Create UNext TF functional core

    Args:
        x (keras.KerasTensor): Input tensor
        params (UNextParams): Model parameters.

    Returns:
        keras.KerasTensor: Output tensor
    """

    y = x

    #### ENCODER ####
    skip_layers: list[keras.layers.Layer | None] = []
    for i, block in enumerate(params.blocks):
        name = f"ENC{i+1}"
        for d in range(block.depth):
            y = UNext_block(
                output_filters=block.filters,
                expand_ratio=block.expand_ratio,
                kernel_size=block.kernel,
                strides=1,
                se_ratio=block.se_ratio,
                dropout=block.dropout,
                norm=block.norm,
                name=f"{name}.D{d+1}",
            )(y)
        # END FOR
        skip_layers.append(y if block.skip else None)

        # Downsample using strided conv
        y = keras.layers.Conv2D(
            filters=block.filters,
            kernel_size=block.pool,
            strides=block.strides,
            padding="same",
            use_bias=block.norm is None,
            kernel_initializer="he_normal",
            kernel_regularizer=keras.regularizers.L2(1e-3),
            name=f"{name}.pool",
        )(y)
        if block.norm == "batch":
            y = keras.layers.BatchNormalization(
                name=f"{name}.norm",
            )(y)
        elif block.norm == "layer":
            ln_axis = 2 if y.shape[1] == 1 else 1 if y.shape[2] == 1 else (1, 2)
            y = keras.layers.LayerNormalization(
                axis=ln_axis,
                name=f"{name}.norm",
            )(y)
        # END IF
    # END FOR

    #### DECODER ####
    for i, block in enumerate(reversed(params.blocks)):
        name = f"DEC{i+1}"
        for d in range(block.ddepth or block.depth):
            y = UNext_block(
                output_filters=block.filters,
                expand_ratio=block.expand_ratio,
                kernel_size=block.kernel,
                strides=1,
                se_ratio=block.se_ratio,
                dropout=block.dropout,
                norm=block.norm,
                name=f"{name}.D{d+1}",
            )(y)
        # END FOR

        # Upsample using transposed conv
        # y = keras.layers.Conv1DTranspose(
        #     filters=block.filters,
        #     kernel_size=block.pool,
        #     strides=block.strides,
        #     padding="same",
        #     kernel_initializer="he_normal",
        #     kernel_regularizer=keras.regularizers.L2(1e-3),
        #     name=f"{name}.unpool",
        # )(y)

        y = keras.layers.Conv2D(
            filters=block.filters,
            kernel_size=block.pool,
            strides=1,
            padding="same",
            use_bias=block.norm is None,
            kernel_initializer="he_normal",
            kernel_regularizer=keras.regularizers.L2(1e-3),
            name=f"{name}.conv",
        )(y)
        y = keras.layers.UpSampling2D(size=block.strides, name=f"{name}.unpool")(y)

        # Skip connection
        skip_layer = skip_layers.pop()
        if skip_layer is not None:
            # y = keras.layers.Concatenate(name=f"{name}.S1.cat")([y, skip_layer])
            y = keras.layers.Add(name=f"{name}.S1.cat")([y, skip_layer])

            # Use conv to reduce filters
            y = keras.layers.Conv2D(
                block.filters,
                kernel_size=1,  # block.kernel,
                padding="same",
                kernel_initializer="he_normal",
                kernel_regularizer=keras.regularizers.L2(1e-3),
                use_bias=block.norm is None,
                name=f"{name}.S1.conv",
            )(y)

            if block.norm == "batch":
                y = keras.layers.BatchNormalization(
                    name=f"{name}.S1.norm",
                )(y)
            elif block.norm == "layer":
                ln_axis = 2 if y.shape[1] == 1 else 1 if y.shape[2] == 1 else (1, 2)
                y = keras.layers.LayerNormalization(
                    axis=ln_axis,
                    name=f"{name}.S1.norm",
                )(y)
            # END IF

            y = keras.layers.Activation(
                "relu6",
                name=f"{name}.S1.relu" if name else None,
            )(y)
        # END IF

        y = UNext_block(
            output_filters=block.filters,
            expand_ratio=block.expand_ratio,
            kernel_size=block.kernel,
            strides=1,
            se_ratio=block.se_ratio,
            dropout=block.dropout,
            norm=block.norm,
            name=f"{name}.D{block.depth+1}",
        )(y)

    # END FOR
    return y


def UNext(
    x: keras.KerasTensor,
    params: UNextParams,
    num_classes: int,
) -> keras.Model:
    """Create UNext TF functional model

    Args:
        x (keras.KerasTensor): Input tensor
        params (UNextParams): Model parameters.
        num_classes (int, optional): # classes.

    Returns:
        keras.Model: Model
    """
    requires_reshape = len(x.shape) == 3
    if requires_reshape:
        y = keras.layers.Reshape((1,) + x.shape[1:])(x)
    else:
        y = x

    y = unext_core(y, params)

    if params.include_top:
        # Add a per-point classification layer
        y = keras.layers.Conv2D(
            num_classes,
            kernel_size=params.output_kernel_size,
            padding="same",
            kernel_initializer="he_normal",
            kernel_regularizer=keras.regularizers.L2(1e-3),
            name="NECK.conv",
            use_bias=True,
        )(y)
        if not params.use_logits:
            y = keras.layers.Softmax()(y)
        # END IF
    # END IF

    if requires_reshape:
        y = keras.layers.Reshape(y.shape[2:])(y)

    # Define the model
    model = keras.Model(x, y, name=params.name)
    return model
