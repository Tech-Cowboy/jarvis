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
from . import WakeDetector, talk_requested

log = logging.getLogger(__name__)


def is_custom_model(wake_word: str) -> bool:
    return wake_word.endswith((".onnx", ".tflite")) or "/" in wake_word


def model_files_present(wake_word: str = "hey_jarvis") -> bool:
    try:
        import openwakeword
    except ImportError:
        return False
    res = Path(openwakeword.__file__).parent / "resources" / "models"
    base_ok = (res / "embedding_model.onnx").is_file() and (res / "melspectrogram.onnx").is_file()
    if is_custom_model(wake_word):
        return Path(wake_word).expanduser().is_file() and base_ok
    return any(res.glob(f"{wake_word}*.onnx")) and base_ok


def download_models(wake_word: str = "hey_jarvis") -> None:
    """Fetch the shared feature models plus a pre-trained wake model (custom models are already on disk)."""
    from openwakeword import utils

    if is_custom_model(wake_word):
        utils.download_models(model_names=["hey_jarvis"])  # brings the embedding and melspectrogram models along
    else:
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
        model_ref = str(Path(wake_word).expanduser()) if is_custom_model(wake_word) else wake_word
        framework = "tflite" if model_ref.endswith(".tflite") else "onnx"
        self.model = Model(wakeword_models=[model_ref], inference_framework=framework)
        self._key = next(iter(self.model.models.keys()))
        self._last_trigger = 0.0

    def wait(self, stop_event, talk_event=None) -> bool:
        self.model.reset()
        self.mic.flush()
        while not stop_event.is_set():
            if talk_requested(talk_event):
                return True
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
        phrase = Path(self.wake_word).stem.replace("_v0.1", "").replace("_", " ")
        return f"wake word '{phrase}' (threshold {self.threshold})"
