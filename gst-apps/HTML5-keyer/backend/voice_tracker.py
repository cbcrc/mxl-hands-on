# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
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

Two Vosk recognizers run on the same audio:
  - a "dictation" recognizer that feeds the general word-follow scroll
    tracking. Once a script is loaded it is also grammar-constrained, to the
    script's own vocabulary (see set_script_vocabulary) — the same
    reliability win the command recognizer gets below, just scoped to
    whatever is actually being read today instead of a fixed phrase list.
    Falls back to open-vocabulary decoding when no script is loaded yet.
  - a grammar-constrained "command" recognizer restricted to a small phrase
    list (the fixed keywords + "story <name>" for each marker, + each {tag}
    phrase, in the currently loaded script). Open-vocabulary decoding across
    a ~100k-word vocabulary is a coin flip for short, out-of-sentence command
    phrases and arbitrary story titles (confirmed live: "go top" -> "go up",
    "two" -> "too"); constraining the decoder to the handful of phrases that
    are actually valid right now is far more reliable, and it adapts
    automatically to whatever story titles/tags are in *today's* script
    without any code change.
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

# Fixed command phrases, per recognition language — only the phrase for the
# *active* language is compiled into the grammar. Including a foreign-language
# phrase would be harmless (it just could never match), but Vosk logs an
# "Ignoring word missing in vocabulary" warning for every word not in the
# active model's lexicon, so keeping this language-specific avoids the noise.
_FIXED_COMMAND_PHRASES_BY_LANG = {
    "en-US": ["go top"],
    "fr-CA": ["debut du texte"],
}


class VoiceTracker:
    """
    Long-lived Vosk recognizers fed PCM from the appsink.

    `broadcast` is a thread-safe callable taking the recognized dictation
    transcript string (feeds the general word-follow matcher); `on_command`
    is a thread-safe callable taking a dict `{"command": ..., "param": ...}`
    fired only on a confident grammar-constrained command match.
    """

    def __init__(
        self,
        broadcast: Callable[[str], None],
        on_command: Callable[[dict], None],
    ) -> None:
        self._broadcast = broadcast
        self._on_command = on_command
        self._lock = threading.Lock()
        self._enabled = False
        self._lang = "en-US"

        self._model = None
        self._model_lang: Optional[str] = None

        self._rec = None            # dictation recognizer (grammar-constrained once a script loads)
        self._last_partial = ""
        self._dictation_vocab: list[str] = []

        self._cmd_rec = None        # grammar-constrained command recognizer
        self._story_names: list[str] = []
        self._tag_names: list[str] = []
        self._tag_phrase_set: set[str] = set()
        self._grammar_phrases: list[str] = list(_FIXED_COMMAND_PHRASES_BY_LANG[self._lang])
        self._cmd_last_partial = ""

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
            # Force the worker to (re)load the model and both recognizers.
            self._model = None
            self._rec = None
            self._cmd_rec = None
            self._rebuild_grammar_phrases_locked()

    def set_story_names(self, names: list[str]) -> None:
        """Rebuild the command grammar for the story markers in the current script."""
        with self._lock:
            self._story_names = list(names)
            self._rebuild_grammar_phrases_locked()

    def set_api_tags(self, tag_names: list[str]) -> None:
        """Rebuild the command grammar with the {tag} phrases in the current script.

        Unlike story names, a tag phrase is not prefixed by a keyword — it's read
        naturally as part of the line — so it's added to the grammar as a bare
        phrase and matched verbatim in _emit_command.
        """
        with self._lock:
            self._tag_names = list(tag_names)
            self._tag_phrase_set = {n.strip().lower() for n in tag_names if n.strip()}
            self._rebuild_grammar_phrases_locked()

    def set_script_vocabulary(self, words: list[str]) -> None:
        """Rebuild the dictation recognizer's grammar from the current script's own words.

        Words of 3 letters or fewer are dropped — the frontend's word-follow
        matcher already ignores them (see matchTranscriptToScript in
        teleprompter.js), so including them here would only bloat the grammar.
        """
        with self._lock:
            vocab = sorted({w.strip().lower() for w in words if len(w.strip()) > 3})
            if vocab == self._dictation_vocab:
                return
            self._dictation_vocab = vocab
            # Force the worker to rebuild the dictation recognizer with the new vocabulary.
            self._rec = None
            self._last_partial = ""

    def _rebuild_grammar_phrases_locked(self) -> None:
        """Recompute self._grammar_phrases from language + story names + tags. Caller must hold self._lock."""
        phrases = list(_FIXED_COMMAND_PHRASES_BY_LANG.get(self._lang, []))
        phrases += [f"story {n.strip().lower()}" for n in self._story_names if n.strip()]
        phrases += sorted(self._tag_phrase_set)
        if phrases == self._grammar_phrases:
            return
        self._grammar_phrases = phrases
        # Force the worker to rebuild the grammar recognizer with the new phrase list.
        self._cmd_rec = None
        self._cmd_last_partial = ""

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self._enabled = bool(on)
            if not on:
                self._drain()
                self._rec = None
                self._cmd_rec = None
                self._last_partial = ""
                self._cmd_last_partial = ""

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
            self._model = None
            self._rec = None
            self._cmd_rec = None
            self._last_partial = ""
            self._cmd_last_partial = ""
            self._story_names = []
            self._tag_names = []
            self._tag_phrase_set = set()
            self._dictation_vocab = []
            self._grammar_phrases = list(_FIXED_COMMAND_PHRASES_BY_LANG.get(self._lang, []))

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

    def _ensure_model(self) -> bool:
        """Lazily load the shared Model for the current language."""
        with self._lock:
            lang = self._lang
            if self._model is not None and self._model_lang == lang:
                return True

        try:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)  # silence Vosk's own chatter
        except Exception as exc:
            log.error("Vosk is not available: %s", exc)
            return False

        path = _MODEL_PATHS[lang]
        try:
            log.info("Loading Vosk model for %s from %s …", lang, path)
            model = Model(path)
        except Exception as exc:
            log.error("Could not load Vosk model %s: %s", path, exc)
            return False

        with self._lock:
            self._model = model
            self._model_lang = lang
            self._rec = None
            self._cmd_rec = None
            self._last_partial = ""
            self._cmd_last_partial = ""
        log.info("Vosk model for %s ready", lang)
        return True

    def _ensure_dictation_recognizer(self) -> bool:
        if not self._ensure_model():
            return False
        with self._lock:
            if self._rec is not None:
                return True
            model = self._model
            vocab = list(self._dictation_vocab)

        from vosk import KaldiRecognizer
        if vocab:
            # "[unk]" is required for the same reason as the command recognizer
            # below — without it, every word outside today's script vocabulary
            # (ad-libs, corrections) would get force-matched to the closest
            # in-vocabulary word instead of being rejected.
            grammar = json.dumps(vocab + ["[unk]"])
            rec = KaldiRecognizer(model, _SAMPLE_RATE, grammar)
        else:
            rec = KaldiRecognizer(model, _SAMPLE_RATE)
        rec.SetWords(False)
        with self._lock:
            self._rec = rec
            self._last_partial = ""
        return True

    def _ensure_command_recognizer(self) -> bool:
        if not self._ensure_model():
            return False
        with self._lock:
            if self._cmd_rec is not None:
                return True
            model = self._model
            phrases = list(self._grammar_phrases)

        from vosk import KaldiRecognizer
        # "[unk]" is required so audio that doesn't match a known phrase is
        # rejected as unknown rather than force-matched to the closest one —
        # without it, every word of normal reading would get mis-recognized
        # as whichever grammar phrase sounds closest.
        grammar = json.dumps(phrases + ["[unk]"])
        try:
            rec = KaldiRecognizer(model, _SAMPLE_RATE, grammar)
        except Exception:
            log.exception("Could not build grammar-constrained command recognizer")
            return False
        with self._lock:
            self._cmd_rec = rec
            self._cmd_last_partial = ""
        return True

    def _run(self) -> None:
        while True:
            pcm = self._q.get()
            if pcm is None:
                continue
            if not self._enabled:
                continue

            if self._ensure_dictation_recognizer():
                self._process_dictation(pcm)

            if self._ensure_command_recognizer():
                self._process_command(pcm)

    def _process_dictation(self, pcm: bytes) -> None:
        rec = self._rec
        if rec is None:
            return
        try:
            if rec.AcceptWaveform(pcm):
                text = self._strip_unk(json.loads(rec.Result()).get("text", ""))
                if text:
                    log.info("Vosk FINAL: %r", text)
                    self._emit(text)
                self._last_partial = ""
            else:
                partial = self._strip_unk(json.loads(rec.PartialResult()).get("partial", ""))
                if partial and partial != self._last_partial:
                    self._last_partial = partial
                    log.info("Vosk partial: %r", partial)
                    self._emit(partial)
        except Exception:
            log.exception("Vosk dictation recognition failed")

    @staticmethod
    def _strip_unk(text: str) -> str:
        """Drop literal "[unk]" tokens the grammar-constrained recognizer emits for
        out-of-vocabulary audio — they're noise to the frontend's word matcher."""
        if "[unk]" not in text:
            return text
        return " ".join(w for w in text.split() if w != "[unk]")

    def _process_command(self, pcm: bytes) -> None:
        rec = self._cmd_rec
        if rec is None:
            return
        try:
            # Only act on FINAL command results — partials from a constrained
            # grammar can still be an incomplete prefix of a longer phrase
            # (e.g. "story" before the rest of the name has arrived), and a
            # discrete jump command isn't latency-sensitive the way scroll
            # following is, so waiting the extra beat for the endpointer is
            # worth it to avoid firing on a half-heard phrase.
            if rec.AcceptWaveform(pcm):
                text = json.loads(rec.Result()).get("text", "")
                if text and text != "[unk]":
                    log.info("Vosk command FINAL: %r", text)
                    self._emit_command(text)
        except Exception:
            log.exception("Vosk command recognition failed")

    def _emit(self, text: str) -> None:
        try:
            self._broadcast(text)
        except Exception:
            log.exception("transcript broadcast failed")

    def _emit_command(self, text: str) -> None:
        norm = text.strip().lower()
        if norm in ("go top", "debut du texte"):
            cmd = {"command": "goTop"}
        elif norm.startswith("story "):
            cmd = {"command": "jumpToStory", "param": norm[len("story "):].strip()}
        else:
            # Unlike "go top"/"story <name>", which are spoken as their own
            # isolated command utterance, a {tag} is read in place inside a
            # normal sentence — the endpointer's FINAL result covers the whole
            # utterance around it, with every non-grammar word collapsed to
            # "[unk]". Stripping those leaves just the words that were
            # actually recognized, in order, so the tag phrase can still be
            # found as a contiguous run rather than needing to be the *entire*
            # utterance verbatim.
            tag = self._find_tag_phrase(self._strip_unk(norm))
            if tag is None:
                return
            cmd = {"command": "triggerApiTag", "param": tag}
        try:
            self._on_command(cmd)
        except Exception:
            log.exception("voice command dispatch failed")

    def _find_tag_phrase(self, text: str) -> Optional[str]:
        padded = f" {text} "
        for tag in sorted(self._tag_phrase_set, key=len, reverse=True):
            if f" {tag} " in padded:
                return tag
        return None
