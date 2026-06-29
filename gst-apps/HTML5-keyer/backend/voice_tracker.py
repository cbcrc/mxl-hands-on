"""
Server-side voice tracking for the teleprompter.

The OGraf teleprompter graphic ships with browser Web Speech API voice tracking,
which cannot run inside the headless CEF render (no microphone device, no cloud
speech backend).  Instead we transcribe the selected MXL audio flow here with
Vosk (offline) and push the recognized text to the prompter page over the
WebSocket hub; the page feeds it into the graphic's existing matcher.

Audio arrives as 16 kHz mono S16LE PCM from the GStreamer appsink (see
gst_keyer._add_audio_branch).  Recognition runs on a dedicated worker thread so
the GStreamer streaming thread is never blocked.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Vosk model directories (populated in the Docker image).
_MODEL_PATHS = {
    "en-US": "/opt/vosk/en",
    "fr-CA": "/opt/vosk/fr",
}
_SAMPLE_RATE = 16000


class VoiceTracker:
    """
    Long-lived Vosk recognizer fed PCM from the appsink.

    `broadcast` is a thread-safe callable taking the recognized transcript
    string; the main app wires it to the prompter WebSocket hub.
    """

    def __init__(self, broadcast: Callable[[str], None]) -> None:
        self._broadcast = broadcast
        self._lock = threading.Lock()
        self._enabled = False
        self._lang = "en-US"

        self._model = None
        self._model_lang: Optional[str] = None
        self._rec = None
        self._last_partial = ""

        self._q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=50)
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # ── Public control ────────────────────────────────────────────────────────

    def set_language(self, lang: str) -> None:
        if lang not in _MODEL_PATHS:
            log.warning("Unknown voice language %r — keeping %s", lang, self._lang)
            return
        with self._lock:
            if lang == self._lang:
                return
            self._lang = lang
            # Force the worker to (re)load the model and reset the recognizer.
            self._rec = None

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self._enabled = bool(on)
            if not on:
                self._drain()
                self._rec = None
                self._last_partial = ""

    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def language(self) -> str:
        with self._lock:
            return self._lang

    def reset(self) -> None:
        """Reset recognizer state between pipeline sessions."""
        with self._lock:
            self._enabled = False
            self._drain()
            self._rec = None
            self._last_partial = ""

    def feed(self, pcm: bytes) -> None:
        """Called from the GStreamer appsink thread; enqueue only when enabled."""
        if not self._enabled:
            return
        try:
            self._q.put_nowait(pcm)
        except queue.Full:
            pass  # drop oldest-style: skip this chunk rather than block the pipeline

    # ── Worker ──────────────────────────────────────────────────────────────--

    def _drain(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def _ensure_recognizer(self) -> bool:
        """Lazily load the model for the current language. Returns True if ready."""
        with self._lock:
            lang = self._lang
            if self._rec is not None and self._model_lang == lang:
                return True

        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel
            SetLogLevel(-1)  # silence Vosk's own chatter
        except Exception as exc:
            log.error("Vosk is not available: %s", exc)
            return False

        path = _MODEL_PATHS[lang]
        try:
            log.info("Loading Vosk model for %s from %s …", lang, path)
            model = Model(path)
            rec = KaldiRecognizer(model, _SAMPLE_RATE)
            rec.SetWords(False)
        except Exception as exc:
            log.error("Could not load Vosk model %s: %s", path, exc)
            return False

        with self._lock:
            self._model = model
            self._model_lang = lang
            self._rec = rec
            self._last_partial = ""
        log.info("Vosk model for %s ready", lang)
        return True

    def _run(self) -> None:
        while True:
            pcm = self._q.get()
            if pcm is None:
                continue
            if not self._enabled:
                continue
            if not self._ensure_recognizer():
                continue
            rec = self._rec
            if rec is None:
                continue
            try:
                if rec.AcceptWaveform(pcm):
                    text = json.loads(rec.Result()).get("text", "")
                    if text:
                        self._emit(text)
                    self._last_partial = ""
                else:
                    partial = json.loads(rec.PartialResult()).get("partial", "")
                    if partial and partial != self._last_partial:
                        self._last_partial = partial
                        self._emit(partial)
            except Exception:
                log.exception("Vosk recognition failed")

    def _emit(self, text: str) -> None:
        try:
            self._broadcast(text)
        except Exception:
            log.exception("transcript broadcast failed")
