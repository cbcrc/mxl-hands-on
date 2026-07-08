// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
export default class TeleprompterGraphic extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.animationFrame = null;
    this.currentScroll = 0; 
    this.targetScroll = 0; 
    this.speed = 2; 
    this.isPlaying = false;
    
    // Voice Tracking State
    this.recognition = null;
    this.voiceTrackingActive = false;
    this.scriptWords = [];
    this.currentWordIndex = 0;
    this.voiceLang = 'en-US'; 

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
               .replace(/[\u0300-\u036f]/g, "") 
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
      const rawTokens = data.scriptText.split(/(\s+)/); 
      let html = '';
      this.scriptWords = [];
      let wordCounter = 0;

      rawTokens.forEach((token) => {
        if (/\S/.test(token)) { 
          const cleanText = this.normalizeText(token);
          this.scriptWords.push(cleanText);
          html += `<span id="word-${wordCounter}" style="transition: color 0.3s;">${token}</span>`;
          wordCounter++;
        } else { 
          html += token.replace(/\n/g, '<br>');
        }
      });
      
      content.innerHTML = html;
      this.currentWordIndex = 0;
    }
    
    if (data.scrollSpeed !== undefined) this.speed = data.scrollSpeed;
    if (data.fontSize !== undefined) this.style.setProperty('--font-size', `${data.fontSize}vw`);
    if (data.enableCountdown !== undefined) this.useCountdown = data.enableCountdown;
    
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
    words.forEach(w => w.style.color = '#ffffff');
    this.targetScroll = this.currentScroll;
  }

  matchTranscriptToScript(transcript) {
    const rawSpokenTokens = transcript.split(/\s+/);
    const spokenWords = [];
    
    rawSpokenTokens.forEach(word => {
      const cleanWord = this.normalizeText(word);
      if (cleanWord.length > 3) spokenWords.push(cleanWord);
    });

    if (spokenWords.length === 0) return;
    
    const lastSpoken = spokenWords[spokenWords.length - 1]; 
    const searchLimit = Math.min(this.scriptWords.length, this.currentWordIndex + 15); 
    
    for (let i = this.currentWordIndex; i < searchLimit; i++) {
      if (this.scriptWords[i] === lastSpoken) {
        const oldWord = this.shadowRoot.getElementById(`word-${this.currentWordIndex}`);
        if (oldWord) oldWord.style.color = '#ffffff';
        this.currentWordIndex = i;
        const newWord = this.shadowRoot.getElementById(`word-${i}`);
        if (newWord) {
           newWord.style.color = '#ffff00'; 
           this.targetScroll = -newWord.offsetTop; 
        }
        break; 
      }
    }
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
        this.animateScroll();
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
        this.animateScroll();
      }
    }
    return { statusCode: 200 };
  }

  async stopAction(params) {
    this.clearCountdown();
    this.isPlaying = false;
    if (this.animationFrame) cancelAnimationFrame(this.animationFrame);
    
    // Hard Reset on Timer & Scrolling
    clearInterval(this.timerInterval);
    this.timerInterval = null;
    this.playStartTime = 0;
    this.updateTimerUI(0);
    
    this.currentScroll = 0;
    this.targetScroll = 0;
    this.currentWordIndex = 0;
    
    const words = this.shadowRoot.querySelectorAll('span[id^="word-"]');
    words.forEach(w => w.style.color = '#ffffff');

    const content = this.shadowRoot.getElementById('content');
    if (content) content.style.transform = `translateY(0px)`;
    
    return { statusCode: 200 };
  }

  async customAction(params) {
    const payloadStr = JSON.stringify(params || "");
    const isPause = payloadStr.includes("pause") || params === "pause";
    const isResume = payloadStr.includes("resume") || params === "resume";
    const isSpeedUp = payloadStr.includes("speedUp") || params === "speedUp";
    const isSpeedDown = payloadStr.includes("speedDown") || params === "speedDown";
    
    if (isPause) {
      this.clearCountdown();
      this.isPlaying = false;
      if (this.animationFrame) cancelAnimationFrame(this.animationFrame);
      // NOTE: Timer is deliberately NOT cleared here!
      return { statusCode: 200 };
    } 
    
    if (isResume) {
      if (!this.isPlaying) {
        this.isPlaying = true;
        this.animateScroll();
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
    this.shadowRoot.innerHTML = '';
    return { statusCode: 200 };
  }

// --- ANIMATION LOOP --- //
  animateScroll() {
    if (!this.isPlaying) return;
    
    const content = this.shadowRoot.getElementById('content');
    if (!content) return;
    
    if (this.voiceTrackingActive) {
       this.currentScroll += (this.targetScroll - this.currentScroll) * 0.05;
    } else {
       this.currentScroll -= this.speed;
       this.targetScroll = this.currentScroll; 
    }
    
    content.style.transform = `translateY(${this.currentScroll}px)`;
    this.animationFrame = requestAnimationFrame(() => this.animateScroll());
  }
} // <-- THIS CLOSING BRACKET IS CRUCIAL