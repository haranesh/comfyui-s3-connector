"""
ComfyUI S3 Connector
====================
A ComfyUI custom node pack for uploading and loading images from S3 buckets.

Nodes:
- S3 Upload Image: Upload images from ComfyUI workflow to S3 bucket
- S3 Load Image: Load images from S3 bucket into ComfyUI workflow
"""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

# Version info
__version__ = "1.0.0"
