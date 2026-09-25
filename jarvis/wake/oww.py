"""openWakeWord detector using the free pre-trained "hey_jarvis" model.

openWakeWord code is Apache-2.0; its pre-trained models are CC BY-NC-SA 4.0
(non-commercial). Fine for a personal assistant; train your own model for
commercial use (see the openWakeWord docs).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ..audio.mic import Microphone
from . import WakeDetector

log = logging.getLogger(__name__)


def model_files_present(wake_word: str = "hey_jarvis") -> bool:
    try:
        import openwakeword
    except ImportError:
        return False
    res = Path(openwakeword.__file__).parent / "resources" / "models"
    return any(res.glob(f"{wake_word}*.onnx")) and (res / "embedding_model.onnx").is_file()


def download_models(wake_word: str = "hey_jarvis") -> None:
    from openwakeword import utils

    utils.download_models(model_names=[wake_word])


class OpenWakeWordDetector(WakeDetector):
    name = "wakeword"

    def __init__(self, mic: Microphone, wake_word: str = "hey_jarvis", threshold: float = 0.5, cooldown: float = 1.5):
        from openwakeword.model import Model

        if not model_files_present(wake_word):
            log.info("downloading openWakeWord models for %s", wake_word)
            download_models(wake_word)
        self.mic = mic
        self.wake_word = wake_word
        self.threshold = threshold
        self.cooldown = cooldown
        self.model = Model(wakeword_models=[wake_word], inference_framework="onnx")
        self._key = next(iter(self.model.models.keys()))
        self._last_trigger = 0.0

    def wait(self, stop_event) -> bool:
        self.model.reset()
        self.mic.flush()
        while not stop_event.is_set():
            frame = self.mic.read_frame(timeout=0.5)
            if frame is None:
                continue
            score = float(self.model.predict(frame)[self._key])
            if score >= self.threshold and time.time() - self._last_trigger > self.cooldown:
                self._last_trigger = time.time()
                log.info("wake word detected (score=%.2f)", score)
                self.model.reset()
                return True
        return False

    def describe(self) -> str:
        return f"wake word '{self.wake_word.replace('_', ' ')}' (threshold {self.threshold})"
