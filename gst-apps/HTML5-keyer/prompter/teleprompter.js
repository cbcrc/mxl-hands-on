// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
// Voice-activated keyword commands. Keys are the trigger phrase with every
// word run through normalizeText() and concatenated with no separator, so
// matching is diacritic/case/punctuation-insensitive like script-word
// matching already is. Extend this table for future keywords.
const VOICE_COMMANDS = {
  gotop: 'goTop',
  debutdutexte: 'goTop',
  // Confirmed from live Vosk logs: without a deliberate pause between the two
  // words, "go top" is sometimes misheard as "go up".
  goup: 'goTop',
};

export default class TeleprompterGraphic extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.animationFrame = null;
    this.scrollLoopRunning = false;
    this.lastFrameTime = 0;
    // Scrolling is time-based: motion per rAF tick is scaled by the elapsed time
    // against this nominal frame duration, so an occasional late/dropped rAF
    // callback no longer surfaces as a stutter on the captured MXL output.
    this.frameMs = 1000 / 60;
    // Voice tracking moves the target in discrete word-sized leaps (a Vosk final
    // can advance several words at once). Cap how fast we chase it — px per
    // nominal frame — so a big leap becomes a steady scroll instead of a snap.
    this.maxFollowPx = 4;
    this.currentScroll = 0;
    this.targetScroll = 0;
    this.speed = 2;
    this.isPlaying = false;
    this.reverseDirection = false;

    // Voice Tracking State
    this.recognition = null;
    this.voiceTrackingActive = false;
    this.scriptWords = [];
    this.currentWordIndex = 0;
    this.voiceLang = 'en-US';
    // Story markers ([STORY: Name] lines) parsed out of the current script —
    // each entry is { name, elementId, wordIndex } so both a manual/API jump
    // and a voice-activated jump ("story <name>") can locate the heading.
    this.storyMarkers = [];

    // Countdown State
    this.useCountdown = true;
    this.isCountingDown = false;
    this.countdownTimer = null;

    // Timer State
    this.playStartTime = 0;
    this.timerInterval = null; // Independent clock interval
  }

  async load(params) {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          width: 100vw;
          height: 100vh;
          background-color: #000000;
          color: #ffffff;
          font-family: "Helvetica Neue", Arial, sans-serif;
          --font-size: 5vw;
          font-size: var(--font-size);
          font-weight: bold;
          overflow: hidden;
          position: relative;
        }

        :host(.mirrored) { transform: scaleX(-1); }

        /* --- STATUS BAR --- */
        .status-bar {
          position: absolute;
          top: 0; left: 0; right: 0;
          height: 5vw;
          background-color: #1a1a1a;
          border-bottom: 2px solid #333;
          display: flex;
          align-items: center;
          justify-content: flex-end;
          padding: 0 5vw;
          font-family: monospace;
          font-size: 2.5vw;
          color: #00ff00;
          z-index: 50;
        }

        .script-container {
          position: absolute;
          top: 15%;
          left: 10%;
          right: 10%;
          padding-bottom: 90vh;
          text-align: left;
          line-height: 1.4;
          overflow-wrap: break-word;
          word-wrap: break-word;
          /* Promote to its own compositor layer so scrolling is a cheap GPU
             translate instead of re-rasterizing the whole text block every
             frame — that per-frame repaint is what made the scroll stutter
             intermittently on the captured output. */
          will-change: transform;
          backface-visibility: hidden;
        }

        .cue-marker {
          position: absolute;
          top: 15%;
          left: 2%;
          width: 0;
          height: 0;
          border-top: 3vw solid transparent;
          border-bottom: 3vw solid transparent;
          border-left: 4.5vw solid #ffffff;
          transform: translateY(-50%);
          z-index: 10;
        }

        /* Reverse-scroll indicator: flips the cue marker to point left so the
           scroll direction is visible in the composited output. Note this
           combines with :host(.mirrored)'s scaleX(-1) — mirrored + reversed
           cancels out visually, which is expected (rare combination). */
        .cue-marker.reversed {
          transform: scaleX(-1) translateY(-50%);
        }

        /* Story marker ([STORY: Name] lines) — rendered as a visible section
           slug, styled distinctly from body copy so it reads as a heading to
           glance at (confirms position) rather than text to speak aloud. */
        .story-marker {
          color: #ffcc33;
          font-weight: 700;
          font-size: 0.55em;
          letter-spacing: 0.05em;
          text-transform: uppercase;
          opacity: 0.85;
          border-top: 1px dashed rgba(255, 255, 255, 0.35);
          padding-top: 0.3em;
          margin-top: 0.6em;
        }

        /* {tag phrase} words — read normally (braces stripped, see updateAction)
           but tinted orange so the on-air talent can see it's wired to an API
           trigger. An inline color (word-follow's active/passed states) always
           wins over this class, so the flash-yellow-while-reading behavior is
           unaffected; those call sites clear the inline color via
           removeProperty('color') instead of forcing white, so a tag word
           settles back to orange rather than white once read past. */
        .tag-word {
          color: #ff9500;
        }

        .countdown-overlay {
          position: absolute;
          top: 0; left: 0; right: 0; bottom: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 30vw;
          color: rgba(255, 255, 255, 0.9);
          background-color: rgba(0, 0, 0, 0.6);
          z-index: 100;
          opacity: 0;
          pointer-events: none;
          transition: opacity 0.2s ease-in-out;
        }

        .countdown-overlay.active {
          opacity: 1;
        }
      </style>

      <div class="status-bar" id="status-bar">
        <span>T+ <span id="timer-display">00:00</span></span>
      </div>
      <div class="cue-marker"></div>
      <div class="script-container" id="content"></div>
      <div class="countdown-overlay" id="countdown"></div>
    `;

    if (params?.data) {
      await this.updateAction({ data: params.data });
    }

    return { statusCode: 200 };
  }

  normalizeText(text) {
    return text.normalize("NFD")
               .replace(/[̀-ͯ]/g, "")
               .replace(/[^a-z0-9]/gi, '')
               .toLowerCase();
  }

  formatTime(ms) {
    const totalSeconds = Math.floor(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
  }

  updateTimerUI(ms) {
    const timerEl = this.shadowRoot.getElementById('timer-display');
    if (timerEl) timerEl.innerText = this.formatTime(ms);
  }

  // INDEPENDENT TIMER LOOP
  startTimerInterval() {
    if (this.timerInterval) return; // Prevent duplicates
    this.timerInterval = setInterval(() => {
      if (this.playStartTime > 0) {
        this.updateTimerUI(Date.now() - this.playStartTime);
      }
    }, 250); // Updates the UI 4 times a second so the flip feels instant
  }

  async updateAction(params) {
    if (!params?.data) return { statusCode: 200 };
    const data = params.data;
    const content = this.shadowRoot.getElementById('content');

    if (data.scriptText !== undefined) {
      const storyMarkerRe = /^\[STORY:\s*(.+?)\]\s*$/i;
      this.scriptWords = [];
      this.storyMarkers = [];
      let wordCounter = 0;
      let storyIdx = 0;
      let html = '';

      data.scriptText.split('\n').forEach((line, lineIdx, lines) => {
        const m = line.match(storyMarkerRe);
        if (m) {
          const name = m[1];
          const elementId = `story-marker-${storyIdx}`;
          this.storyMarkers.push({ name, elementId, wordIndex: wordCounter });
          const escaped = name.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
          html += `<div class="story-marker" id="${elementId}">${escaped}</div>`;
          storyIdx++;
          return;
        }

        // Split the line into alternating outside-tag / {tag phrase} segments
        // so tag words render without the braces (mirrors main.py's
        // _parse_script_vocabulary) and pick up the .tag-word highlight.
        const tagRe = /\{([^{}]+)\}/g;
        const segments = [];
        let lastEnd = 0;
        let tm;
        while ((tm = tagRe.exec(line))) {
          if (tm.index > lastEnd) segments.push({ text: line.slice(lastEnd, tm.index), isTag: false });
          segments.push({ text: tm[1], isTag: true });
          lastEnd = tagRe.lastIndex;
        }
        if (lastEnd < line.length) segments.push({ text: line.slice(lastEnd), isTag: false });

        segments.forEach(({ text: segText, isTag }) => {
          const rawTokens = segText.split(/(\s+)/);
          rawTokens.forEach((token) => {
            if (/\S/.test(token)) {
              const cleanText = this.normalizeText(token);
              this.scriptWords.push(cleanText);
              const cls = isTag ? ' class="tag-word"' : '';
              html += `<span id="word-${wordCounter}"${cls} style="transition: color 0.3s;">${token}</span>`;
              wordCounter++;
            } else {
              html += token;
            }
          });
        });
        if (lineIdx < lines.length - 1) html += '<br>';
      });

      content.innerHTML = html;

      // Loading a new script returns the prompter to a clean, paused-at-top
      // state (UI "Load Script" or POST /prompter-api/update).  Without this the
      // container keeps the translateY() offset and isPlaying state from a prior
      // run, so the new script either keeps scrolling or renders off-screen —
      // looking like the text never arrived and forcing a pipeline restart.
      this.isPlaying = false;
      this.clearCountdown();
      if (this.animationFrame) cancelAnimationFrame(this.animationFrame);
      this.scrollLoopRunning = false;
      clearInterval(this.timerInterval);
      this.timerInterval = null;
      this.playStartTime = 0;
      this.updateTimerUI(0);
      this.currentScroll = 0;
      this.targetScroll = 0;
      this.currentWordIndex = 0;
      content.style.transform = 'translateY(0px)';
    }

    if (data.scrollSpeed !== undefined) this.speed = data.scrollSpeed;
    if (data.fontSize !== undefined) this.style.setProperty('--font-size', `${data.fontSize}vw`);
    if (data.enableCountdown !== undefined) this.useCountdown = data.enableCountdown;

    if (data.reverseScroll !== undefined) {
      this.reverseDirection = data.reverseScroll;
      const cueMarker = this.shadowRoot.querySelector('.cue-marker');
      if (cueMarker) cueMarker.classList.toggle('reversed', this.reverseDirection);
    }

    if (data.showStatusBar !== undefined) {
      const statusBar = this.shadowRoot.getElementById('status-bar');
      if (statusBar) statusBar.style.display = data.showStatusBar ? 'flex' : 'none';
    }

    if (data.voiceLanguage !== undefined) {
      this.voiceLang = data.voiceLanguage;
      if (this.voiceTrackingActive) {
        this.stopVoiceTracking();
        this.startVoiceTracking();
      }
    }

    if (data.mirrored !== undefined) {
       data.mirrored ? this.classList.add('mirrored') : this.classList.remove('mirrored');
    }

    if (data.enableVoiceTracking !== undefined) {
      if (data.enableVoiceTracking && !this.voiceTrackingActive) {
        this.startVoiceTracking();
      } else if (!data.enableVoiceTracking && this.voiceTrackingActive) {
        this.stopVoiceTracking();
      }
    }

    return { statusCode: 200 };
  }

  // --- VOICE TRACKING LOGIC --- //
  startVoiceTracking() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    this.recognition = new SpeechRecognition();
    this.recognition.continuous = true;
    this.recognition.interimResults = true;
    this.recognition.lang = this.voiceLang;

    this.recognition.onstart = () => { this.voiceTrackingActive = true; };
    this.recognition.onerror = (event) => { if (event.error === 'not-allowed') this.voiceTrackingActive = false; };

    this.recognition.onresult = (event) => {
      if (!this.isPlaying) return;
      let interimTranscript = '';
      let finalTranscript = '';

      for (let i = event.resultIndex; i < event.results.length; ++i) {
        event.results[i].isFinal ? finalTranscript += event.results[i][0].transcript : interimTranscript += event.results[i][0].transcript;
      }
      this.matchTranscriptToScript(finalTranscript + ' ' + interimTranscript);
    };

    this.recognition.onend = () => {
      if (this.voiceTrackingActive) {
        setTimeout(() => { try { this.recognition.start(); } catch(e) {} }, 100);
      }
    };

    try { this.recognition.start(); } catch(e) {}
  }

  stopVoiceTracking() {
    this.voiceTrackingActive = false;
    if (this.recognition) this.recognition.stop();
    const words = this.shadowRoot.querySelectorAll('span[id^="word-"]');
    words.forEach(w => w.style.removeProperty('color'));
    this.targetScroll = this.currentScroll;
  }

  // Externally-supplied transcript (server-side Vosk over the WebSocket).
  // The headless CEF render has no microphone / Web Speech backend, so the
  // backend transcribes the MXL audio flow and pushes the text here, reusing
  // the same matcher the browser Web Speech path would have used.
  pushTranscript(text) {
    if (!text) return;
    this.voiceTrackingActive = true;
    this.matchTranscriptToScript(text);
  }

  // Reset to the very top of the script — a live "quick jump", not a Stop:
  // playback/voice tracking keep running. Used by the "go top"/"debut du
  // texte" voice keyword so the anchor can rehearse from the start during a
  // break, then voice-jump to a story marker (jumpToStory) to get back live.
  goTop() {
    const oldWord = this.shadowRoot.getElementById(`word-${this.currentWordIndex}`);
    if (oldWord) oldWord.style.removeProperty('color');
    this.currentWordIndex = 0;
    this.currentScroll = 0;
    this.targetScroll = 0;
    const content = this.shadowRoot.getElementById('content');
    if (content) content.style.transform = 'translateY(0px)';
  }

  // Jump straight to a named [STORY: Name] marker (manual/API or voice
  // "story <name>"). Scrolls to the marker heading's own position (not the
  // next word) so the visible slug lands at the cue line, confirming the
  // anchor is in the right section before the body copy below it.
  jumpToStory(name) {
    if (!name) return;
    const key = this.normalizeText(name);
    const marker = this.storyMarkers.find(m => this.normalizeText(m.name) === key);
    if (!marker) return;
    const oldWord = this.shadowRoot.getElementById(`word-${this.currentWordIndex}`);
    if (oldWord) oldWord.style.removeProperty('color');
    this.currentWordIndex = marker.wordIndex;
    const markerEl = this.shadowRoot.getElementById(marker.elementId);
    const content = this.shadowRoot.getElementById('content');
    if (markerEl && content) {
      this.targetScroll = -markerEl.offsetTop;
      this.currentScroll = this.targetScroll; // snap immediately — the rAF loop may not be running
      content.style.transform = `translate3d(0, ${this.currentScroll}px, 0)`;
    }
  }

  // Dispatch from the backend's grammar-constrained command recognizer (see
  // voice_tracker.py) — a {"type":"voice_command"} WS message, distinct from
  // the general {"type":"transcript"} path. This is the primary, reliable
  // route for these two commands (Vosk decoding among a handful of known
  // phrases rather than guessing across the open vocabulary); the fuzzy
  // checkVoiceCommands() below still runs on every dictation transcript as a
  // redundant fallback, so either recognizer firing is enough.
  applyVoiceCommand(command, param) {
    if (command === 'goTop') this.goTop();
    else if (command === 'jumpToStory') this.jumpToStory(param);
  }

  // Checks a spoken transcript for a keyword command ("go top"/"debut du
  // texte") or a "story <name>" story-jump phrase. Returns true if a command
  // fired, so the caller can skip treating the same transcript as a
  // script-word match.
  checkVoiceCommands(normalizedTokens) {
    const joined2 = normalizedTokens.slice(-2).join('');
    const joined3 = normalizedTokens.slice(-3).join('');
    if (VOICE_COMMANDS[joined2] === 'goTop' || VOICE_COMMANDS[joined3] === 'goTop') {
      this.goTop();
      return true;
    }

    // Match the words immediately following "story", trying growing windows
    // (1-3 words) rather than requiring the name to be the end of the
    // utterance — a transcript chunk can include trailing words spoken after
    // the story name (e.g. "story sports now").
    const storyIdx = normalizedTokens.lastIndexOf('story');
    if (storyIdx !== -1) {
      for (let span = 1; span <= 3 && storyIdx + span <= normalizedTokens.length; span++) {
        const nameKey = normalizedTokens.slice(storyIdx + 1, storyIdx + 1 + span).join('');
        const marker = this.storyMarkers.find(m => this.normalizeText(m.name) === nameKey);
        if (marker) {
          this.jumpToStory(marker.name);
          return true;
        }
      }
    }

    return false;
  }

  matchTranscriptToScript(transcript) {
    // Voice tracking drives the scroll on its own — there is no Play press in
    // this mode — so make sure the animation loop is running to follow the
    // target the matcher sets below.
    if (this.voiceTrackingActive) this.ensureAnimating();

    const rawSpokenTokens = transcript.split(/\s+/);
    const spokenWords = [];
    // Unfiltered normalized tokens for keyword/command matching — the >3-char
    // filter below exists to reduce false-positive *script*-word matches, but
    // it would also silently drop short command words like "go"/"top"/"du".
    const normalizedTokens = [];

    rawSpokenTokens.forEach(word => {
      const cleanWord = this.normalizeText(word);
      if (!cleanWord) return;
      normalizedTokens.push(cleanWord);
      if (cleanWord.length > 3) spokenWords.push(cleanWord);
    });

    if (this.voiceTrackingActive && this.checkVoiceCommands(normalizedTokens)) return;

    if (spokenWords.length === 0) return;

    const lastSpoken = spokenWords[spokenWords.length - 1];
    const prevSpoken = spokenWords.length > 1 ? spokenWords[spokenWords.length - 2] : null;
    const searchLimit = Math.min(this.scriptWords.length, this.currentWordIndex + 15);

    // A single recognized word is enough evidence for a small advance (normal
    // reading cadence), but not for a far jump: the grammar-constrained
    // recognizer maps near-miss audio onto script words, so one isolated hit
    // used to yank the prompter forward — including to the *second* instance
    // of a word that appears twice in the window. A jump beyond NEAR_JUMP
    // words now also requires the previously spoken word to match the script
    // word just before the candidate, so duplicates are disambiguated by
    // their context and a lone false detection can't move the target far.
    const NEAR_JUMP = 3;
    for (let i = this.currentWordIndex; i < searchLimit; i++) {
      if (this.scriptWords[i] !== lastSpoken) continue;
      if (i - this.currentWordIndex > NEAR_JUMP && !this.matchesPrecedingWord(i, prevSpoken)) continue;
      const oldWord = this.shadowRoot.getElementById(`word-${this.currentWordIndex}`);
      if (oldWord) oldWord.style.removeProperty('color');
      this.currentWordIndex = i;
      const newWord = this.shadowRoot.getElementById(`word-${i}`);
      if (newWord) {
         newWord.style.color = '#ffff00';
         this.targetScroll = -newWord.offsetTop;
      }
      break;
    }
  }

  // True when prevSpoken matches the script word preceding index i. Words of
  // 3 letters or fewer are skipped: they are filtered out of the spoken
  // stream above, so the previous *spoken* word corresponds to the nearest
  // preceding *long* script word, not necessarily scriptWords[i - 1].
  matchesPrecedingWord(i, prevSpoken) {
    if (!prevSpoken) return false;
    let j = i - 1;
    while (j >= 0 && this.scriptWords[j].length <= 3) j--;
    return j >= 0 && this.scriptWords[j] === prevSpoken;
  }

  // --- COUNTDOWN LOGIC --- //
  startCountdownSequence() {
    this.isCountingDown = true;
    const countdownEl = this.shadowRoot.getElementById('countdown');
    let count = 3;

    countdownEl.innerText = count;
    countdownEl.classList.add('active');

    this.countdownTimer = setInterval(() => {
      count--;
      if (count > 0) {
        countdownEl.innerText = count;
      } else {
        this.clearCountdown();
        this.isPlaying = true;
        if (!this.playStartTime) this.playStartTime = Date.now(); // Record actual start time
        this.startTimerInterval();
        this.ensureAnimating();
      }
    }, 1000);
  }

  clearCountdown() {
    if (this.countdownTimer) {
      clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    }
    this.isCountingDown = false;
    const countdownEl = this.shadowRoot.getElementById('countdown');
    if (countdownEl) countdownEl.classList.remove('active');
  }

  // --- STANDARD OGRAF CONTROLS --- //
  async playAction(params) {
    if (!this.isPlaying && !this.isCountingDown) {
      // Only do the countdown if it's the very first time starting
      if (this.useCountdown && this.playStartTime === 0) {
        this.startCountdownSequence();
      } else {
        this.isPlaying = true;
        if (!this.playStartTime) this.playStartTime = Date.now();
        this.startTimerInterval();
        this.ensureAnimating();
      }
    }
    return { statusCode: 200 };
  }

  async stopAction(params) {
    this.clearCountdown();
    this.isPlaying = false;
    if (this.animationFrame) cancelAnimationFrame(this.animationFrame);
    this.scrollLoopRunning = false;

    // Hard Reset on Timer & Scrolling
    clearInterval(this.timerInterval);
    this.timerInterval = null;
    this.playStartTime = 0;
    this.updateTimerUI(0);

    this.currentScroll = 0;
    this.targetScroll = 0;
    this.currentWordIndex = 0;

    const words = this.shadowRoot.querySelectorAll('span[id^="word-"]');
    words.forEach(w => w.style.removeProperty('color'));

    const content = this.shadowRoot.getElementById('content');
    if (content) content.style.transform = `translateY(0px)`;

    return { statusCode: 200 };
  }

  async customAction(params) {
    if (params && typeof params === 'object' && params.action === 'jumpToStory') {
      this.jumpToStory(params.param);
      return { statusCode: 200 };
    }

    const payloadStr = JSON.stringify(params || "");
    const isPause = payloadStr.includes("pause") || params === "pause";
    const isResume = payloadStr.includes("resume") || params === "resume";
    const isSpeedUp = payloadStr.includes("speedUp") || params === "speedUp";
    const isSpeedDown = payloadStr.includes("speedDown") || params === "speedDown";

    if (isPause) {
      this.clearCountdown();
      this.isPlaying = false;
      if (this.animationFrame) cancelAnimationFrame(this.animationFrame);
      this.scrollLoopRunning = false;
      // NOTE: Timer is deliberately NOT cleared here!
      return { statusCode: 200 };
    }

    if (isResume) {
      if (!this.isPlaying) {
        this.isPlaying = true;
        this.ensureAnimating();
      }
      return { statusCode: 200 };
    }

    if (isSpeedUp) { this.speed += 0.5; return { statusCode: 200 }; }
    if (isSpeedDown) { this.speed = Math.max(0.5, this.speed - 0.5); return { statusCode: 200 }; }

    return { statusCode: 404 };
  }

  async dispose(params) {
    this.clearCountdown();
    clearInterval(this.timerInterval);
    this.stopVoiceTracking();
    this.isPlaying = false;
    if (this.animationFrame) cancelAnimationFrame(this.animationFrame);
    this.scrollLoopRunning = false;
    this.shadowRoot.innerHTML = '';
    return { statusCode: 200 };
  }

// --- ANIMATION LOOP --- //
  // Start the rAF loop if it isn't already running. Auto-scroll (Play) and voice
  // tracking share one loop, so this guards against two chains running at once
  // (which would otherwise double the scroll speed).
  ensureAnimating() {
    if (this.scrollLoopRunning) return;
    this.scrollLoopRunning = true;
    this.lastFrameTime = 0;
    this.animationFrame = requestAnimationFrame((t) => this.animateScroll(t));
  }

  animateScroll(now) {
    // Run while auto-scrolling OR while voice tracking is steering the scroll.
    if ((!this.isPlaying && !this.voiceTrackingActive) || !this.shadowRoot.getElementById('content')) {
      this.scrollLoopRunning = false;
      this.animationFrame = null;
      return;
    }

    const content = this.shadowRoot.getElementById('content');

    if (typeof now !== 'number') now = performance.now();
    let dt = this.lastFrameTime ? now - this.lastFrameTime : this.frameMs;
    this.lastFrameTime = now;
    // Clamp so a long stall (GC, hidden page) catches up in one bounded step
    // instead of leaping across the whole script.
    if (!(dt > 0)) dt = this.frameMs;
    if (dt > 100) dt = 100;
    const frames = dt / this.frameMs;

    if (this.voiceTrackingActive) {
       // Frame-rate-independent exponential smoothing toward the voice target,
       // with a velocity cap so a multi-word leap scrolls smoothly rather than
       // snapping forward.
       const k = 1 - Math.pow(1 - 0.05, frames);
       const maxStep = this.maxFollowPx * frames;
       let step = (this.targetScroll - this.currentScroll) * k;
       if (step > maxStep) step = maxStep;
       else if (step < -maxStep) step = -maxStep;
       this.currentScroll += step;
    } else {
       // Time-based constant velocity: rAF jitter no longer judders the output.
       const dir = this.reverseDirection ? 1 : -1;
       this.currentScroll += dir * this.speed * frames;
       this.targetScroll = this.currentScroll;
    }

    // translate3d keeps the element on the GPU/compositor layer (paired with
    // will-change: transform) so each frame is a translate, not a repaint.
    content.style.transform = `translate3d(0, ${this.currentScroll}px, 0)`;
    this.animationFrame = requestAnimationFrame((t) => this.animateScroll(t));
  }
} // <-- THIS CLOSING BRACKET IS CRUCIAL
