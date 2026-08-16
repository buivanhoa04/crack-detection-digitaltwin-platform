"""
Preprocessing Pipeline — Tiền xử lý ảnh chuyên dụng cho Drone AI Inspection.

3 chế độ xử lý theo mức độ nặng nhẹ:
  - "stream"  : CLAHE + Morphological Shadow + Light Sharpen (ultra-fast, ~3-5ms)
  - "offline" : CLAHE + DoG + Retinex + Adaptive Sharpen (~8-15ms)
  - "batch"   : Retinex + DoG + CLAHE + Edge Enhancement + Noise Suppression (~15-25ms)

Sử dụng thuần OpenCV + NumPy, KHÔNG thêm dependency mới.
"""

import cv2
import numpy as np
import logging
import torch
import torchvision.transforms.functional as TF
import os


logger = logging.getLogger("crack_api")


# ============================================================
# STAGE 1: CLAHE (Contrast Limited Adaptive Histogram Equalization)
# ============================================================
def apply_clahe(frame: np.ndarray, clip_limit: float = 2.0, tile_size: int = 8) -> np.ndarray:
    """
    Tăng tương phản cục bộ trên kênh L (Lightness) của LAB color space.
    Crack mảnh trên nền asphalt đồng nhất sẽ nổi bật hơn rõ rệt.
    """
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
    l = clahe.apply(l)
    enhanced = cv2.merge([l, a, b])
    return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)


# ============================================================
# STAGE 2a: Shadow Suppression — Morphological Opening (Fast)
# ============================================================
def remove_shadow_morphological(frame: np.ndarray, kernel_size: int = 21) -> np.ndarray:
    """
    Ước lượng nền chiếu sáng (illumination) bằng Morphological Opening,
    sau đó trừ đi để loại bỏ bóng cây/cột/mái.
    
    Cực nhanh, phù hợp cho realtime stream.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    
    # Morphological opening = estimate illumination background
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    bg = cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel)
    
    # Normalize: loại bỏ thành phần chiếu sáng không đều
    # Tránh chia cho 0
    bg_float = bg.astype(np.float32) + 1.0
    gray_float = gray.astype(np.float32)
    normalized = (gray_float / bg_float * 128.0).clip(0, 255).astype(np.uint8)
    
    # Reconstruct 3-channel từ normalized grayscale
    # Giữ tỉ lệ màu gốc, chỉ sửa lightness
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    result = cv2.merge([h, s, normalized])
    return cv2.cvtColor(result, cv2.COLOR_HSV2BGR)


# ============================================================
# STAGE 2b: Shadow Suppression — Difference of Gaussians (Quality)
# ============================================================
def remove_shadow_dog(frame: np.ndarray, sigma_small: float = 1.0, sigma_large: float = 20.0) -> np.ndarray:
    """
    Difference of Gaussians: Giữ lại edge frequency cao (crack),
    loại bỏ illumination frequency thấp (bóng, ánh sáng không đều).
    
    Chất lượng cao hơn Morphological nhưng chậm hơn ~2x.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    
    # Kernel size phải là lẻ
    k_small = int(sigma_small * 6) | 1
    k_large = int(sigma_large * 6) | 1
    
    blur_small = cv2.GaussianBlur(gray, (k_small, k_small), sigma_small)
    blur_large = cv2.GaussianBlur(gray, (k_large, k_large), sigma_large)
    
    # DoG = high-freq edges
    dog = blur_small - blur_large
    
    # Normalize to 0-255
    dog_norm = cv2.normalize(dog, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    
    # Blend DoG edge info back vào ảnh gốc (additive enhancement)
    gray_uint8 = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blended = cv2.addWeighted(gray_uint8, 0.7, dog_norm, 0.3, 0)
    
    # Reconstruct 3-channel giữ tỉ lệ màu gốc
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    result = cv2.merge([h, s, blended])
    return cv2.cvtColor(result, cv2.COLOR_HSV2BGR)


# ============================================================
# STAGE 3: Multi-Scale Retinex (MSR)
# ============================================================
def apply_retinex(frame: np.ndarray, sigmas: list = None) -> np.ndarray:
    """
    Multi-Scale Retinex: Tách reflectance (bề mặt vật liệu) khỏi illumination
    (ánh sáng chiếu). Cực mạnh cho UAV imagery với ánh sáng thay đổi liên tục.
    
    Nguyên lý: log(Image) - log(GaussianBlur(Image)) ở nhiều tỷ lệ sigma.
    """
    if sigmas is None:
        sigmas = [15, 80, 250]
    
    img_float = frame.astype(np.float64) + 1.0  # Tránh log(0)
    retinex = np.zeros_like(img_float)
    
    for sigma in sigmas:
        k = int(sigma * 6) | 1  # Kernel size lẻ
        blur = cv2.GaussianBlur(img_float, (k, k), sigma)
        retinex += np.log10(img_float) - np.log10(blur + 1.0)
    
    retinex /= len(sigmas)
    
    # Normalize mỗi kênh riêng về 0-255
    for i in range(3):
        channel = retinex[:, :, i]
        min_val, max_val = channel.min(), channel.max()
        if max_val - min_val > 0:
            retinex[:, :, i] = (channel - min_val) / (max_val - min_val) * 255.0
        else:
            retinex[:, :, i] = 128.0
    
    return retinex.clip(0, 255).astype(np.uint8)


# ============================================================
# STAGE 3.5: Bilateral Filter (Noise Suppression with Edge Preservation)
# ============================================================
def apply_bilateral_filter(frame: np.ndarray, d: int = 9, sigma_color: float = 75.0, sigma_space: float = 75.0) -> np.ndarray:
    """
    Bilateral Filter: Khử nhiễu bề mặt (như hạt cát, sỏi) nhưng giữ nguyên biên của các vết nứt.
    """
    return cv2.bilateralFilter(frame, d, sigma_color, sigma_space)


# ============================================================
# STAGE 4: Unsharp Mask / Edge Enhancement
# ============================================================
def apply_sharpen(frame: np.ndarray, amount: float = 1.0, radius: float = 3.0) -> np.ndarray:
    """
    Unsharp Mask: Làm sắc biên crack mà không amplify noise quá mức.
    amount: Mức độ sharpen (1.0 = nhẹ, 2.0 = mạnh)
    radius: Bán kính blur (sigma)
    """
    k = int(radius * 2) | 1
    blurred = cv2.GaussianBlur(frame, (k, k), radius)
    sharpened = cv2.addWeighted(frame, 1.0 + amount, blurred, -amount, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


# ============================================================
# STAGE 5: Noise Suppression (Batch mode only)
# ============================================================
def apply_denoise(frame: np.ndarray, strength: int = 5) -> np.ndarray:
    """
    Non-local Means Denoising: Khử noise sensor camera drone.
    Chỉ dùng cho batch mode vì khá nặng (~10ms).
    """
    return cv2.fastNlMeansDenoisingColored(frame, None, strength, strength, 7, 21)


# ============================================================
# MAIN PIPELINE ENTRY POINT
# ============================================================

# Cấu hình pipeline cho từng mode
PIPELINE_CONFIGS = {
    "stream": {
        "description": "Ultra-fast cho WebSocket/RTSP realtime",
        "steps": ["clahe", "shadow_morphological", "sharpen_light"], # Đã bỏ bilateral filter
    },
    "offline": {
        "description": "Balance accuracy/speed cho Video Offline",
        "steps": ["clahe", "shadow_dog", "retinex", "sharpen"], # Đã bỏ bilateral filter
    },
    "batch": {
        "description": "Maximum accuracy cho Image API / Drone Survey",
        "steps": ["denoise", "bilateral", "retinex", "shadow_dog", "clahe", "sharpen"],
    },
}


def preprocess_frame(frame: np.ndarray, mode: str = "stream", config: dict = None) -> np.ndarray:
    """
    Hàm chính: Tiền xử lý ảnh theo mode (stream / offline / batch).
    
    Args:
        frame: BGR image (numpy array)
        mode: "stream" | "offline" | "batch"
        config: Override tham số (optional). Keys:
            - clahe_clip: float (default 2.0)
            - clahe_tile: int (default 8)
            - shadow_kernel: int (default 21)
            - retinex_sigmas: list (default [15, 80, 250])
            - sharpen_amount: float (default 1.5)
            - sharpen_radius: float (default 3.0)
    
    Returns:
        Enhanced BGR image (same shape)
    """
    if frame is None or frame.size == 0:
        return frame
    
    if config is None:
        config = {}
    
    pipeline = PIPELINE_CONFIGS.get(mode, PIPELINE_CONFIGS["stream"])
    steps = pipeline["steps"]
    
    result = frame.copy()
    
    for step in steps:
        try:
            if step == "clahe":
                result = apply_clahe(
                    result,
                    clip_limit=config.get("clahe_clip", 2.0),
                    tile_size=config.get("clahe_tile", 8),
                )
            elif step == "shadow_morphological":
                result = remove_shadow_morphological(
                    result,
                    kernel_size=config.get("shadow_kernel", 21),
                )
            elif step == "shadow_dog":
                result = remove_shadow_dog(result)
            elif step == "bilateral":
                result = apply_bilateral_filter(
                    result,
                    d=config.get("bilateral_d", 9),
                    sigma_color=config.get("bilateral_color", 75.0),
                    sigma_space=config.get("bilateral_space", 75.0),
                )
            elif step == "retinex":
                result = apply_retinex(
                    result,
                    sigmas=config.get("retinex_sigmas", [15, 80, 250]),
                )
            elif step == "sharpen":
                result = apply_sharpen(
                    result,
                    amount=config.get("sharpen_amount", 1.0),
                    radius=config.get("sharpen_radius", 3.0),
                )
            elif step == "sharpen_light":
                result = apply_sharpen(result, amount=0.8, radius=2.0)
            elif step == "denoise":
                result = apply_denoise(
                    result,
                    strength=config.get("denoise_strength", 5),
                )
        except Exception as e:
            logger.warning(f"[Preprocess] Step '{step}' failed: {e} — skipping")
            continue
    
    return result

# ============================================================
# GPU PREPROCESSOR (PyTorch) - Ultra Fast for Offline Mode
# ============================================================
class GPUPreprocessor:
    def __init__(self, device="cuda:0"):
        # Fix cho trường hợp device_name truyền vào là "0" thay vì "cuda:0"
        if str(device).strip() == "0":
            device = "cuda:0"
            
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        logger.info(f"🚀 Khởi tạo GPUPreprocessor trên: {self.device}")

    @torch.no_grad()
    def process(self, frame_bgr: np.ndarray, config: dict = None) -> np.ndarray:
        """
        Thực hiện toàn bộ luồng 'offline' (CLAHE -> DoG -> Retinex -> Sharpen)
        nhưng 90% khối lượng công việc được chuyển vào GPU VRAM thông qua PyTorch.
        """
        if config is None: config = {}
        
        # 1. CLAHE (Rất nhanh trên CPU, ~3ms)
        lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = self.clahe.apply(l)
        enhanced_bgr = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
        
        # 1.5 Bilateral filter on CPU (Bỏ qua để tối ưu tốc độ cho GPU pipeline)
        # d = config.get("bilateral_d", 9)
        # color = config.get("bilateral_color", 75.0)
        # space = config.get("bilateral_space", 75.0)
        # enhanced_bgr = cv2.bilateralFilter(enhanced_bgr, d, color, space)
        
        # Đẩy lên GPU (CHW, float32, 0.0 - 1.0)
        # Sử dụng torch.from_numpy để tối ưu bộ nhớ
        tensor_img = torch.from_numpy(enhanced_bgr).permute(2, 0, 1).float().div_(255.0).to(self.device)
        
        # 2. Shadow Suppression (Difference of Gaussians)
        tensor_img = self._apply_dog(tensor_img, config)
        
        # 3. Multi-Scale Retinex
        tensor_img = self._apply_retinex(tensor_img, config)
        
        # 4. Unsharp Mask
        tensor_img = self._apply_sharpen(tensor_img, config)
        
        # Kéo về CPU (HWC, uint8, 0 - 255)
        result_np = tensor_img.mul_(255.0).permute(1, 2, 0).byte().cpu().numpy()
        return result_np

    def _apply_dog(self, img_t: torch.Tensor, config: dict) -> torch.Tensor:
        sigma_small = 1.0
        sigma_large = 20.0
        k_small = int(sigma_small * 6) | 1
        k_large = int(sigma_large * 6) | 1
        
        # Grayscale: Y = 0.299 R + 0.587 G + 0.114 B
        # img_t is BGR, so 0=B, 1=G, 2=R
        gray = 0.114 * img_t[0:1] + 0.587 * img_t[1:2] + 0.299 * img_t[2:3]
        blur_small = self._fast_blur(img_t, 1.0)
        blur_large = self._fast_blur(img_t, 20.0)
        
        dog = blur_small - blur_large
        return torch.clamp(img_t + dog, 0.0, 1.0)

    def _apply_retinex(self, img_t: torch.Tensor, config: dict) -> torch.Tensor:
        sigmas = [15.0, 80.0, 250.0]
        img_log = torch.log1p(img_t * 255.0)
        
        retinex = torch.zeros_like(img_log)
        for s in sigmas:
            blur = self._fast_blur(img_t, s)
            blur_log = torch.log1p(blur * 255.0)
            retinex += (img_log - blur_log)
            
        retinex = retinex / len(sigmas)
        
        mean = torch.mean(retinex)
        std = torch.std(retinex)
        min_val = mean - 2.0 * std
        max_val = mean + 2.0 * std
        
        retinex = (retinex - min_val) / (max_val - min_val + 1e-6)
        return torch.clamp(retinex, 0.0, 1.0)

    def _fast_blur(self, img_t: torch.Tensor, sigma: float) -> torch.Tensor:
        if sigma <= 2.0:
            k = int(sigma * 6) | 1
            return TF.gaussian_blur(img_t, [k, k], [sigma, sigma])
        
        # Với sigma lớn, downsample -> blur -> upsample để tối ưu hàng vạn lần tốc độ
        scale = max(1.0, sigma / 2.0) # Thu nhỏ tỉ lệ thuận với sigma
        
        # Downsample
        small = torch.nn.functional.interpolate(img_t.unsqueeze(0), scale_factor=1.0/scale, mode='bilinear', align_corners=False)
        
        # Blur trên ảnh nhỏ với sigma = 2.0
        k = int(2.0 * 6) | 1
        blurred_small = TF.gaussian_blur(small, [k, k], [2.0, 2.0])
        
        # Upsample về kích thước cũ
        blurred = torch.nn.functional.interpolate(blurred_small, size=img_t.shape[1:], mode='bilinear', align_corners=False)
        return blurred.squeeze(0)

    def _apply_sharpen(self, img_t: torch.Tensor, config: dict) -> torch.Tensor:
        amount = config.get("sharpen_amount", 1.0) if config else 1.0
        blur = self._fast_blur(img_t, 2.0)
        sharpened = img_t + amount * (img_t - blur)
        return torch.clamp(sharpened, 0.0, 1.0)


# ============================================================
# STAGE 5: Super-Resolution Enhancer (Real-ESRGAN Compact / SwinIR)
# ============================================================
class SuperResolutionEnhancer:
    """
    Super-Resolution module to upscale low-resolution image regions 4x
    using ONNX Runtime with GPU support and automatic OpenCV bicubic fallback.
    """
    def __init__(self, model_path="weights/realesrgan_compact.onnx", device="cuda"):
        self.model_path = model_path
        self.device = device
        self.sess = None
        
        if os.path.exists(self.model_path):
            try:
                import onnxruntime as ort
                # Use CUDA if requested and available, else fallback to CPU
                providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if device == "cuda" else ['CPUExecutionProvider']
                sess_opts = ort.SessionOptions()
                self.sess = ort.InferenceSession(self.model_path, sess_opts, providers=providers)
                logger.info(f"✅ Loaded Super-Resolution model from {self.model_path} with device={device}")
            except Exception as e:
                logger.error(f"⚠️ Failed to load Super-Resolution model: {e}. Fallback to OpenCV Bicubic.")
        else:
            logger.warning(f"⚠️ Super-Resolution model not found at {self.model_path}. Fallback to OpenCV Bicubic.")

    def enhance(self, tile: np.ndarray) -> np.ndarray:
        """
        Upscales input tile image by 4x.
        Args:
            tile: BGR numpy array H x W x 3
        Returns:
            upscaled: 4H x 4W x 3 BGR image
        """
        if tile is None or tile.size == 0:
            return tile
            
        if self.sess is None:
            # Fallback to bicubic interpolation
            h, w = tile.shape[:2]
            return cv2.resize(tile, (w * 4, h * 4), interpolation=cv2.INTER_CUBIC)
            
        try:
            # Preprocessing: BGR -> RGB, HWC -> CHW, [0, 1] scale
            img = cv2.cvtColor(tile, cv2.COLOR_BGR2RGB)
            img = img.transpose((2, 0, 1)).astype(np.float32) / 255.0
            img = np.expand_dims(img, axis=0)  # Shape: 1, 3, H, W
            
            # ONNX Run
            input_name = self.sess.get_inputs()[0].name
            output_name = self.sess.get_outputs()[0].name
            outputs = self.sess.run([output_name], {input_name: img})
            
            # Postprocessing: Remove batch, clip, scale, CHW -> HWC, RGB -> BGR
            out_img = outputs[0][0]  # Shape: 3, 4H, 4W
            out_img = np.clip(out_img * 255.0, 0, 255).astype(np.uint8)
            out_img = out_img.transpose((1, 2, 0))
            out_img = cv2.cvtColor(out_img, cv2.COLOR_RGB2BGR)
            return out_img
        except Exception as e:
            logger.error(f"⚠️ Error running Super-Resolution inference: {e}. Fallback to Bicubic.")
            h, w = tile.shape[:2]
            return cv2.resize(tile, (w * 4, h * 4), interpolation=cv2.INTER_CUBIC)

