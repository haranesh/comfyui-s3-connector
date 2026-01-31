import os
import io
import numpy as np
from PIL import Image

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

import torch
import folder_paths

# Load environment variables from .env file in the node directory
NODE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(NODE_DIR, ".env")
load_dotenv(ENV_PATH)


def get_s3_client():
    """Create and return an S3 client using configuration from .env"""
    endpoint_url = os.getenv("S3_ENDPOINT_URL")
    if endpoint_url == "" or endpoint_url is None:
        endpoint_url = None

    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
        region_name=os.getenv("S3_REGION", "us-east-1"),
        endpoint_url=endpoint_url,
    )


def get_bucket_name():
    """Get the S3 bucket name from configuration"""
    return os.getenv("S3_BUCKET_NAME")


def get_s3_prefix():
    """Get the S3 prefix from configuration"""
    prefix = os.getenv("S3_PREFIX", "")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


class S3UploadImage:
    """ComfyUI node to upload images to S3 bucket"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "folder_path": ("STRING", {"default": ""}),
                "file_name": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING",)
    RETURN_NAMES = ("s3_url", "path",)
    FUNCTION = "upload_image"
    CATEGORY = "S3 Connector"
    OUTPUT_NODE = True

    def upload_image(self, images, folder_path, file_name):
        """Upload image(s) to S3 bucket"""
        s3_client = get_s3_client()
        bucket_name = get_bucket_name()
        prefix = get_s3_prefix()

        if not bucket_name:
            raise ValueError("S3_BUCKET_NAME not configured in .env file")

        if not file_name:
            raise ValueError("File name is required")

        # Clean inputs
        folder_path = folder_path.strip().strip("/")
        file_name = file_name.strip()

        results = []

        for i, image in enumerate(images):
            # Convert tensor to PIL Image
            # ComfyUI images are in format [B, H, W, C] with values 0-1
            img_np = (image.cpu().numpy() * 255).astype(np.uint8)
            pil_image = Image.fromarray(img_np)

            # Generate filename with index if multiple images
            if len(images) > 1:
                name, ext = os.path.splitext(file_name)
                if not ext:
                    ext = ".png"
                filename = f"{name}_{i}{ext}"
            else:
                filename = file_name if "." in file_name else f"{file_name}.png"

            # Build S3 key
            if folder_path:
                s3_key = f"{prefix}{folder_path}/{filename}"
            else:
                s3_key = f"{prefix}{filename}"

            # Clean up any double slashes
            s3_key = s3_key.replace("//", "/")

            # Convert PIL image to bytes
            img_buffer = io.BytesIO()
            pil_image.save(img_buffer, format="PNG")
            img_buffer.seek(0)

            # Upload to S3
            try:
                s3_client.upload_fileobj(
                    img_buffer,
                    bucket_name,
                    s3_key,
                    ExtraArgs={"ContentType": "image/png"}
                )

                # Generate URL
                endpoint_url = os.getenv("S3_ENDPOINT_URL")
                if endpoint_url:
                    s3_url = f"{endpoint_url.rstrip('/')}/{bucket_name}/{s3_key}"
                else:
                    region = os.getenv("S3_REGION", "us-east-1")
                    s3_url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{s3_key}"

                results.append((s3_url, s3_key))
                print(f"[S3 Connector] Uploaded: {s3_key}")

            except ClientError as e:
                raise RuntimeError(f"Failed to upload to S3: {str(e)}")

        # Return the last uploaded file's info
        if results:
            return results[-1]
        return ("", "")


class S3LoadImage:
    """ComfyUI node to load images from S3 bucket"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "folder_path": ("STRING", {"default": ""}),
                "file_name": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK",)
    RETURN_NAMES = ("image", "mask",)
    FUNCTION = "load_image"
    CATEGORY = "S3 Connector"

    def load_image(self, folder_path, file_name):
        """Load image from S3 bucket"""
        s3_client = get_s3_client()
        bucket_name = get_bucket_name()
        prefix = get_s3_prefix()

        if not bucket_name:
            raise ValueError("S3_BUCKET_NAME not configured in .env file")

        if not file_name:
            raise ValueError("File name is required")

        # Build the S3 key from folder_path and file_name
        folder_path = folder_path.strip().strip("/")
        file_name = file_name.strip()

        if folder_path:
            s3_key = f"{prefix}{folder_path}/{file_name}"
        else:
            s3_key = f"{prefix}{file_name}"

        # Clean up any double slashes
        s3_key = s3_key.replace("//", "/")

        try:
            # Download from S3
            response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
            image_data = response["Body"].read()

            # Convert to PIL Image
            pil_image = Image.open(io.BytesIO(image_data))

            # Handle different image modes
            if pil_image.mode == "I":
                pil_image = pil_image.point(lambda i: i * (1 / 255)).convert("L")

            has_alpha = "A" in pil_image.mode

            # Convert to RGB if needed
            if pil_image.mode != "RGB":
                if has_alpha:
                    pil_image = pil_image.convert("RGBA")
                else:
                    pil_image = pil_image.convert("RGB")

            # Convert to numpy array and normalize
            img_np = np.array(pil_image).astype(np.float32) / 255.0

            # Create image tensor [B, H, W, C]
            if has_alpha:
                # Separate RGB and Alpha
                image_tensor = torch.from_numpy(img_np[:, :, :3])[None,]
                mask = 1.0 - torch.from_numpy(img_np[:, :, 3])
            else:
                image_tensor = torch.from_numpy(img_np)[None,]
                # Create empty mask
                mask = torch.zeros((img_np.shape[0], img_np.shape[1]), dtype=torch.float32)

            print(f"[S3 Connector] Loaded: {s3_key}")
            return (image_tensor, mask.unsqueeze(0))

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "NoSuchKey":
                raise RuntimeError(f"Image not found in S3: {s3_key}")
            raise RuntimeError(f"Failed to load from S3: {str(e)}")


class S3UploadImageFullPath:
    """ComfyUI node to upload images to S3 bucket using full path"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "full_path": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING",)
    RETURN_NAMES = ("s3_url", "path",)
    FUNCTION = "upload_image"
    CATEGORY = "S3 Connector"
    OUTPUT_NODE = True

    def upload_image(self, images, full_path):
        """Upload image(s) to S3 bucket"""
        s3_client = get_s3_client()
        bucket_name = get_bucket_name()
        prefix = get_s3_prefix()

        if not bucket_name:
            raise ValueError("S3_BUCKET_NAME not configured in .env file")

        if not full_path:
            raise ValueError("Full path is required")

        # Clean input
        full_path = full_path.strip().strip("/")

        results = []

        for i, image in enumerate(images):
            # Convert tensor to PIL Image
            # ComfyUI images are in format [B, H, W, C] with values 0-1
            img_np = (image.cpu().numpy() * 255).astype(np.uint8)
            pil_image = Image.fromarray(img_np)

            # Generate filename with index if multiple images
            if len(images) > 1:
                name, ext = os.path.splitext(full_path)
                if not ext:
                    ext = ".png"
                path = f"{name}_{i}{ext}"
            else:
                path = full_path if "." in full_path else f"{full_path}.png"

            # Build S3 key
            s3_key = f"{prefix}{path}"

            # Clean up any double slashes
            s3_key = s3_key.replace("//", "/")

            # Convert PIL image to bytes
            img_buffer = io.BytesIO()
            pil_image.save(img_buffer, format="PNG")
            img_buffer.seek(0)

            # Upload to S3
            try:
                s3_client.upload_fileobj(
                    img_buffer,
                    bucket_name,
                    s3_key,
                    ExtraArgs={"ContentType": "image/png"}
                )

                # Generate URL
                endpoint_url = os.getenv("S3_ENDPOINT_URL")
                if endpoint_url:
                    s3_url = f"{endpoint_url.rstrip('/')}/{bucket_name}/{s3_key}"
                else:
                    region = os.getenv("S3_REGION", "us-east-1")
                    s3_url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{s3_key}"

                results.append((s3_url, s3_key))
                print(f"[S3 Connector] Uploaded: {s3_key}")

            except ClientError as e:
                raise RuntimeError(f"Failed to upload to S3: {str(e)}")

        # Return the last uploaded file's info
        if results:
            return results[-1]
        return ("", "")


class GetJobID:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {},
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("job_id",)
    FUNCTION = "fetch_id"
    CATEGORY = "S3 Connector"

    def fetch_id(self, prompt=None, extra_pnginfo=None):
        job_id = "Unknown"

        # 1. Try to get it from the prompt dictionary (The most direct way)
        if prompt is not None:
            # When running from API or UI, Comfy often adds this to extra_data
            job_id = prompt.get("extra_data", {}).get("batch_id", "")
        
        # 2. Try to get it from extra_pnginfo (Metadata-based)
        if not job_id and extra_pnginfo is not None:
            job_id = extra_pnginfo.get("prompt_id", "")

        # 3. Final Fallback: Ask the server instance directly for the current job
        if not job_id or job_id == "Unknown":
            import server
            prompt_server = server.PromptServer.instance
            # The server tracks the current execution ID here
            job_id = getattr(prompt_server, "last_prompt_id", "No ID Found")

        return (str(job_id),)

    # This ensures the node re-runs every time you press "Queue Prompt"
    # Essential for Blackwell/5090 speed so it doesn't cache an old ID
    @classmethod
    def IS_CHANGED(s, **kwargs):
        return float("NaN")

NODE_CLASS_MAPPINGS = {"GetJobID": GetJobID}
NODE_DISPLAY_NAME_MAPPINGS = {"GetJobID": "Get Current Job ID (Final)"}

class S3LoadImageFullPath:
    """ComfyUI node to load images from S3 bucket using full path"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "full_path": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK",)
    RETURN_NAMES = ("image", "mask",)
    FUNCTION = "load_image"
    CATEGORY = "S3 Connector"

    def load_image(self, full_path):
        """Load image from S3 bucket"""
        s3_client = get_s3_client()
        bucket_name = get_bucket_name()
        prefix = get_s3_prefix()

        if not bucket_name:
            raise ValueError("S3_BUCKET_NAME not configured in .env file")

        if not full_path:
            raise ValueError("Full path is required")

        # Build the S3 key from full_path
        full_path = full_path.strip().strip("/")
        s3_key = f"{prefix}{full_path}"

        # Clean up any double slashes
        s3_key = s3_key.replace("//", "/")

        try:
            # Download from S3
            response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
            image_data = response["Body"].read()

            # Convert to PIL Image
            pil_image = Image.open(io.BytesIO(image_data))

            # Handle different image modes
            if pil_image.mode == "I":
                pil_image = pil_image.point(lambda i: i * (1 / 255)).convert("L")

            has_alpha = "A" in pil_image.mode

            # Convert to RGB if needed
            if pil_image.mode != "RGB":
                if has_alpha:
                    pil_image = pil_image.convert("RGBA")
                else:
                    pil_image = pil_image.convert("RGB")

            # Convert to numpy array and normalize
            img_np = np.array(pil_image).astype(np.float32) / 255.0

            # Create image tensor [B, H, W, C]
            if has_alpha:
                # Separate RGB and Alpha
                image_tensor = torch.from_numpy(img_np[:, :, :3])[None,]
                mask = 1.0 - torch.from_numpy(img_np[:, :, 3])
            else:
                image_tensor = torch.from_numpy(img_np)[None,]
                # Create empty mask
                mask = torch.zeros((img_np.shape[0], img_np.shape[1]), dtype=torch.float32)

            print(f"[S3 Connector] Loaded: {s3_key}")
            return (image_tensor, mask.unsqueeze(0))

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "NoSuchKey":
                raise RuntimeError(f"Image not found in S3: {s3_key}")
            raise RuntimeError(f"Failed to load from S3: {str(e)}")


# Node mappings for ComfyUI
NODE_CLASS_MAPPINGS = {
    "S3UploadImage": S3UploadImage,
    "S3LoadImage": S3LoadImage,
    "S3UploadImageFullPath": S3UploadImageFullPath,
    "S3LoadImageFullPath": S3LoadImageFullPath,
    "GetJobID": GetJobID,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "S3UploadImage": "S3 Upload Image",
    "S3LoadImage": "S3 Load Image",
    "S3UploadImageFullPath": "S3 Upload Image (Full Path)",
    "S3LoadImageFullPath": "S3 Load Image (Full Path)",
    "GetJobID": "Get Job ID",
}
