import asyncio
import base64
import json
import os
import traceback
import uuid

import aiohttp

from astrbot import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.datetime_utils import generate_timestamp_id

from ..entities import ProviderType
from ..provider import TTSProvider
from ..register import register_provider_adapter


@register_provider_adapter(
    "volcengine_tts",
    "火山引擎 TTS",
    provider_type=ProviderType.TEXT_TO_SPEECH,
)
class ProviderVolcengineTTS(TTSProvider):
    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        self.api_key = provider_config.get("api_key", "")
        self.appid = provider_config.get("appid", "")
        self.cluster = provider_config.get("volcengine_cluster", "")
        self.voice_type = provider_config.get("volcengine_voice_type", "")
        self.speed_ratio = provider_config.get("volcengine_speed_ratio", 1.0)
        self.resource_id = provider_config.get("volcengine_resource_id", "seed-tts-2.0")
        self.api_base = provider_config.get(
            "api_base",
            "https://openspeech.bytedance.com/api/v1/tts",
        )
        self.timeout = provider_config.get("timeout", 20)

    def _build_request_payload(self, text: str) -> dict:
        return {
            "app": {
                "appid": self.appid,
                "token": self.api_key,
                "cluster": self.cluster,
            },
            "user": {"uid": str(uuid.uuid4())},
            "audio": {
                "voice_type": self.voice_type,
                "encoding": "mp3",
                "speed_ratio": self.speed_ratio,
                "volume_ratio": 1.0,
                "pitch_ratio": 1.0,
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": text,
                "text_type": "plain",
                "operation": "query",
                "with_frontend": 1,
                "frontend_type": "unitTson",
            },
        }

    async def get_audio(self, text: str) -> str:
        """异步方法获取语音文件路径"""
        if not self.appid or "/api/v3/" in self.api_base:
            return await self._get_audio_v3(text)
        return await self._get_audio_v1(text)

    async def _get_audio_v1(self, text: str) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer; {self.api_key}",
        }

        payload = self._build_request_payload(text)

        logger.debug(f"请求头: {headers}")
        logger.debug(f"请求 URL: {self.api_base}")
        logger.debug(f"请求体: {json.dumps(payload, ensure_ascii=False)[:100]}...")

        try:
            async with (
                aiohttp.ClientSession(headers=self.request_headers) as session,
                session.post(
                    self.api_base,
                    data=json.dumps(payload),
                    headers=headers,
                    timeout=self.timeout,
                ) as response,
            ):
                logger.debug(f"响应状态码: {response.status}")

                response_text = await response.text()
                logger.debug(f"响应内容: {response_text[:200]}...")

                if response.status == 200:
                    resp_data = json.loads(response_text)

                    if "data" in resp_data:
                        audio_data = base64.b64decode(resp_data["data"])

                        temp_dir = get_astrbot_temp_path()
                        os.makedirs(temp_dir, exist_ok=True)
                        file_path = os.path.join(
                            temp_dir,
                            f"volcengine_tts_{generate_timestamp_id()}.mp3",
                        )

                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(
                            None,
                            lambda: open(file_path, "wb").write(audio_data),
                        )

                        return file_path
                    error_msg = resp_data.get("message", "未知错误")
                    raise Exception(f"火山引擎 TTS API 返回错误: {error_msg}")
                raise Exception(
                    f"火山引擎 TTS API 请求失败: {response.status}, {response_text}",
                )

        except Exception as e:
            error_details = traceback.format_exc()
            logger.debug(f"火山引擎 TTS 异常详情: {error_details}")
            raise Exception(f"火山引擎 TTS 异常: {e!s}")

    async def _get_audio_v3(self, text: str) -> str:
        """Call the new Volcengine Seed-TTS v3 API (API Key auth)."""
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        payload = {
            "user": {"uid": "astrbot"},
            "req_params": {
                "text": text,
                "speaker": self.voice_type,
                "audio_params": {"format": "mp3", "sample_rate": 24000},
            },
        }

        try:
            async with aiohttp.ClientSession(headers=self.request_headers) as session:
                async with session.post(
                    self.api_base,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                ) as response:
                    body = await response.text()
                    if response.status != 200:
                        raise Exception(
                            f"火山引擎 TTS v3 请求失败: {response.status}, {body[:300]}"
                        )

                    chunks: list[bytes] = []
                    for line in body.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        code = item.get("code", 0)
                        if code not in (0, 20000000, None):
                            raise Exception(
                                f"火山引擎 TTS v3 返回错误: {code} {item.get('message', '')}"
                            )
                        audio_b64 = item.get("data")
                        if audio_b64:
                            chunks.append(base64.b64decode(audio_b64))

                    if not chunks:
                        raise Exception("火山引擎 TTS v3 未返回音频数据")

                    audio_data = b"".join(chunks)
                    temp_dir = get_astrbot_temp_path()
                    os.makedirs(temp_dir, exist_ok=True)
                    file_path = os.path.join(
                        temp_dir,
                        f"volcengine_tts_{generate_timestamp_id()}.mp3",
                    )
                    with open(file_path, "wb") as f:
                        f.write(audio_data)
                    return file_path
        except Exception as e:
            logger.debug(f"火山引擎 TTS v3 异常详情: {traceback.format_exc()}")
            raise Exception(f"火山引擎 TTS v3 异常: {e!s}") from e
