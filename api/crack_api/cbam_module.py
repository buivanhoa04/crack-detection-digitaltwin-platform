"""
CBAM Attention Module & Dynamic Registration for Ultralytics YOLO Bridge Segment Model.
Must be imported BEFORE any YOLO('weights/crack_bridge.pt') calls!
"""

import sys
import torch
import torch.nn as nn
import logging

logger = logging.getLogger("crack_api.cbam")

class ChannelAttention(nn.Module):
    def __init__(self, channels, reduction=16):
        super().__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.max_pool = nn.AdaptiveMaxPool2d(1)
        reduction_channels = max(1, channels // reduction)
        self.fc = nn.Sequential(
            nn.Conv2d(channels, reduction_channels, 1, bias=False),
            nn.ReLU(),
            nn.Conv2d(reduction_channels, channels, 1, bias=False)
        )
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avg_out = self.fc(self.avg_pool(x))
        max_out = self.fc(self.max_pool(x))
        out = avg_out + max_out
        return self.sigmoid(out) * x


class SpatialAttention(nn.Module):
    def __init__(self, kernel_size=7):
        super().__init__()
        self.conv = nn.Conv2d(2, 1, kernel_size, padding=kernel_size//2, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avg_out = torch.mean(x, dim=1, keepdim=True)
        max_out, _ = torch.max(x, dim=1, keepdim=True)
        out = torch.cat([avg_out, max_out], dim=1)
        out = self.conv(out)
        return self.sigmoid(out) * x


class CBAM(nn.Module):
    def __init__(self, channels, reduction=16, kernel_size=7):
        super().__init__()
        self.channel_attention = ChannelAttention(channels, reduction)
        self.spatial_attention = SpatialAttention(kernel_size)

    def forward(self, x):
        x = self.channel_attention(x)
        x = self.spatial_attention(x)
        return x


def register_cbam():
    """Register CBAM module into __main__ and ultralytics.nn.modules for PyTorch unpickling."""
    import __main__
    setattr(__main__, 'CBAM', CBAM)
    setattr(__main__, 'ChannelAttention', ChannelAttention)
    setattr(__main__, 'SpatialAttention', SpatialAttention)

    try:
        import ultralytics.nn.modules as modules
        setattr(modules, 'CBAM', CBAM)
        setattr(modules, 'ChannelAttention', ChannelAttention)
        setattr(modules, 'SpatialAttention', SpatialAttention)
    except Exception as e:
        logger.warning(f"Could not register CBAM in ultralytics.nn.modules: {e}")

    # Register in sys.modules
    sys.modules['CBAM'] = CBAM
    sys.modules['ChannelAttention'] = ChannelAttention
    sys.modules['SpatialAttention'] = SpatialAttention
    logger.info("✅ CBAM Attention module dynamically registered for PyTorch model loading.")

# Auto-register on import
register_cbam()
