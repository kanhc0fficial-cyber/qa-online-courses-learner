import requests
import json
import time
import re
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from .llm_client import LLMClient
import logging

logger = logging.getLogger(__name__)

# Constants
DEFAULT_MAX_RETRIES = 3
RATE_LIMIT_WAIT_TIME = 25  # seconds
DEFAULT_WAIT_TIME = 2  # seconds
MAX_WAIT_TIME = 60  # seconds
RETRYABLE_STATUS_CODES = {408, 409, 425, 429}

class GenericOpenAIAPIClient(LLMClient):
    def __init__(self, api_key: str, api_url: str, max_retries: int = DEFAULT_MAX_RETRIES,
                 api_key_header: str = "Authorization"):
        if max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        self.api_key = api_key
        self.base_url = api_url.rstrip('/')  # Remove trailing slash if present
        self.generate_url = f"{self.base_url}/chat/completions"
        self.max_retries = max_retries
        self.api_key_header = api_key_header

    def generate(self,
        prompt: str,
        image_path: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        stream: bool = False,
        model: str = "llama3.2-vision",
        temperature: float = 0.2,
        num_predict: int = 256) -> Dict[Any, Any]:
        """Generate response from OpenAI-compatible API."""
        # Prepare request content
        paths = image_paths or ([image_path] if image_path else [])
        if paths:
            content = [{"type": "text", "text": prompt}]
            for path in paths:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{self.encode_image(path)}"}
                })
        else:
            content = prompt

        # Prepare request data
        data = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "stream": stream,
            "temperature": temperature,
            "max_tokens": num_predict
        }
        if response_format:
            data["response_format"] = response_format

        # Prepare headers
        credential = f"Bearer {self.api_key}" if self.api_key_header.lower() == "authorization" else self.api_key
        headers = {
            self.api_key_header: credential,
            "HTTP-Referer": "https://github.com/byjlw/video-analyzer",
            "X-Title": "Video Analyzer",
            "Content-Type": "application/json"
        }

        # Try request with retries
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    self.generate_url, headers=headers, json=data, timeout=(20, 180)
                )
                response.raise_for_status()
                
                # Parse successful response
                try:
                    json_response = response.json()
                    if 'error' in json_response:
                        raise Exception(f"API error: {json_response['error']}")
                    
                    if stream:
                        return self._handle_streaming_response(response)
                    
                    if 'choices' not in json_response or not json_response['choices']:
                        raise Exception("No choices in response")
                        
                    message = json_response['choices'][0].get('message', {})
                    if not message or 'content' not in message:
                        raise Exception("No content in response message")
                        
                    return {
                        "response": message['content'],
                        "model": json_response.get("model", model),
                        "usage": json_response.get("usage"),
                        "finish_reason": json_response["choices"][0].get("finish_reason"),
                    }
                    
                except json.JSONDecodeError:
                    raise Exception(f"Invalid JSON response: {response.text}")
                    
            except Exception as exc:
                attempts = attempt + 1
                if attempts == self.max_retries or not self._is_retryable(exc):
                    raise RuntimeError(
                        f"API request failed after {attempts} attempt(s): {exc}"
                    ) from exc

                wait_time = self._retry_wait_seconds(exc, attempt)
                logger.warning(
                    "Request failed (attempt %s/%s): %s",
                    attempts,
                    self.max_retries,
                    exc,
                )
                logger.warning("Waiting %.1f seconds before retry", wait_time)
                time.sleep(wait_time)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (requests.exceptions.ConnectionError,
                            requests.exceptions.Timeout)):
            return True
        if isinstance(exc, requests.exceptions.HTTPError):
            response = exc.response
            if response is None:
                return False
            status = response.status_code
            return status in RETRYABLE_STATUS_CODES or 500 <= status < 600
        # A successful HTTP response with malformed or incomplete JSON can be a
        # transient upstream failure, so preserve the historical retry behavior.
        return True

    @staticmethod
    def _retry_wait_seconds(exc: Exception, attempt: int) -> float:
        default_wait = min(DEFAULT_WAIT_TIME * (2 ** attempt), MAX_WAIT_TIME)
        if not isinstance(exc, requests.exceptions.HTTPError):
            return default_wait
        response = exc.response
        if response is None or response.status_code != 429:
            return default_wait
        value = response.headers.get("Retry-After")
        if not value:
            return RATE_LIMIT_WAIT_TIME
        try:
            return min(max(float(value), 0.0), MAX_WAIT_TIME)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return min(max(delay, 0.0), MAX_WAIT_TIME)
            except (TypeError, ValueError, OverflowError):
                logger.warning("Invalid Retry-After header; using default rate-limit wait")
                return RATE_LIMIT_WAIT_TIME

    def _handle_streaming_response(self, response: requests.Response) -> Dict[Any, Any]:
        """Handle streaming response from API.
        
        Args:
            response: Streaming response from API
            
        Returns:
            Dict containing accumulated response
        """
        accumulated_response = ""
        for line in response.iter_lines():
            if line:
                try:
                    json_response = json.loads(line.decode('utf-8'))
                    if 'choices' in json_response and len(json_response['choices']) > 0:
                        delta = json_response['choices'][0].get('delta', {})
                        if 'content' in delta:
                            accumulated_response += delta['content']
                except json.JSONDecodeError:
                    continue

        return {"response": accumulated_response}
