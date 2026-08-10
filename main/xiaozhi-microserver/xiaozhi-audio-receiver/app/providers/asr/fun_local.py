"""Local FunASR / SenseVoice ASR (batch PCM → text)."""

from __future__ import annotations

import io
import sys
import time
from typing import Any

from loguru import logger

from app.providers.asr.base import ASRProviderBase
from app.providers.asr.utils import extract_transcript, lang_tag_filter

MAX_RETRIES = 2
RETRY_DELAY = 1


class CaptureOutput:
    def __enter__(self):
        self._output = io.StringIO()
        self._original_stdout = sys.stdout
        sys.stdout = self._output
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        sys.stdout = self._original_stdout
        self.output = self._output.getvalue()
        self._output.close()
        if self.output:
            logger.info(self.output.strip())


class FunASR(ASRProviderBase):
    """Requires optional deps: funasr, torch (see requirements-funasr.txt)."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        cfg = self.config
        self.model_dir = cfg.get("model_dir") or (
            "../../xiaozhi-server/models/SenseVoiceSmall"
        )
        self.language = cfg.get("language") or "auto"

        try:
            import psutil

            min_mem = 2 * 1024 * 1024 * 1024
            total = psutil.virtual_memory().total
            if total < min_mem:
                logger.error(
                    f"FunASR: RAM < 2GB ({total / (1024 * 1024):.0f} MB); may fail"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"FunASR memory check skipped: {exc}")

        from funasr import AutoModel

        with CaptureOutput():
            self.model = AutoModel(
                model=self.model_dir,
                vad_kwargs={"max_single_segment_time": 30000},
                disable_update=True,
                hub="hf",
            )

    def recognize(self, pcm: bytes, *, sample_rate: int = 16000) -> str:
        if not pcm:
            return ""
        retry = 0
        while retry < MAX_RETRIES:
            try:
                start = time.time()
                result = self.model.generate(
                    input=pcm,
                    cache={},
                    language=self.language,
                    use_itn=True,
                    batch_size_s=60,
                )
                raw = result[0]["text"] if result else ""
                text = extract_transcript(lang_tag_filter(raw))
                logger.debug(
                    f"FunASR {time.time() - start:.3f}s sample_rate={sample_rate} "
                    f"text={text!r}"
                )
                return text
            except OSError as exc:
                retry += 1
                if retry >= MAX_RETRIES:
                    logger.error(f"FunASR failed after retries: {exc}")
                    return ""
                logger.warning(f"FunASR retry {retry}/{MAX_RETRIES}: {exc}")
                time.sleep(RETRY_DELAY)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"FunASR failed: {exc}")
                return ""
        return ""
