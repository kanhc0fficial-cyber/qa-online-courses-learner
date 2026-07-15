from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
import base64
from io import BytesIO
from PIL import Image

class LLMClient(ABC):
    def encode_image(self, image_path: str) -> str:
        with Image.open(image_path) as image:
            image = image.convert("RGB")
            if max(image.size) > 1600:
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            encoded = BytesIO()
            image.save(encoded, format="JPEG", quality=88, optimize=True)
            return base64.b64encode(encoded.getvalue()).decode("utf-8")

    @abstractmethod
    def generate(self,
        prompt: str,
        image_path: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        stream: bool = False,
        model: str = "llama3.2-vision",
        temperature: float = 0.2,
        num_predict: int = 256) -> Dict[Any, Any]:
        pass
