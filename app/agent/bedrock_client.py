"""boto3 Bedrock Runtime Converse client with async wrapping and error mapping."""

from __future__ import annotations

import asyncio
import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from app.agent.errors import (
    AgentError,
    ModelConfigurationError,
    ModelThrottledError,
    ModelTimeoutError,
    ModelUnavailableError,
)

log = logging.getLogger(__name__)

_THROTTLE = {"ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException"}
_UNAVAILABLE = {
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelNotReadyException",
    "ModelErrorException",
}
_CONFIG = {"AccessDeniedException", "ResourceNotFoundException", "UnrecognizedClientException"}


class BedrockConverseClient:
    def __init__(
        self,
        *,
        region: str,
        model_id: str,
        max_tokens: int,
        temperature: float,
        connect_timeout_s: float,
        read_timeout_s: float,
        max_attempts: int,
        max_concurrency: int,
    ) -> None:
        import boto3
        from botocore.config import Config

        self._model_id = model_id  # foundation model ID or inference profile ID/ARN
        self._inference = {"maxTokens": max_tokens, "temperature": temperature}
        self._client = boto3.client(
            "bedrock-runtime",
            config=Config(
                region_name=region,
                connect_timeout=connect_timeout_s,
                read_timeout=read_timeout_s,
                retries={"mode": "adaptive", "max_attempts": max_attempts},
                max_pool_connections=max_concurrency,
            ),
        )
        # Dedicated pool: the default executor is only cpu+4 threads (5 on 1 vCPU).
        self._executor = ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="bedrock")
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def _call(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        from botocore.exceptions import (
            ClientError,
            ConnectionClosedError,
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
        )

        try:
            return cast(dict[str, Any], self._client.converse(**kwargs))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            log.warning("bedrock client error", extra={"error_code": code})
            if code in _THROTTLE:
                raise ModelThrottledError(code) from exc
            if code == "ModelTimeoutException":
                raise ModelTimeoutError(code) from exc
            if code in _UNAVAILABLE:
                raise ModelUnavailableError(code) from exc
            if code in _CONFIG:
                raise ModelConfigurationError(code) from exc
            raise AgentError(code) from exc
        except (ReadTimeoutError, ConnectTimeoutError) as exc:
            raise ModelTimeoutError(type(exc).__name__) from exc
        except (EndpointConnectionError, ConnectionClosedError) as exc:
            raise ModelUnavailableError(type(exc).__name__) from exc

    async def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool_config: dict[str, Any] | None,
        timeout_s: float,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "system": [{"text": system}],
            "messages": messages,
            "inferenceConfig": self._inference,
        }
        if tool_config:
            kwargs["toolConfig"] = tool_config
        async with self._semaphore:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(self._executor, functools.partial(self._call, kwargs))
            try:
                return await asyncio.wait_for(future, timeout_s)
            except TimeoutError as exc:
                # The worker thread finishes in the background; botocore read_timeout bounds it.
                raise ModelTimeoutError("converse exceeded time budget") from exc

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
