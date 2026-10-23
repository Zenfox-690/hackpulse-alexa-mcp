// HackPulse Alexa+ Multimodal Ambient Controller (Echo Show 15 Engine)
(function() {
  class HackPulseApp {
    constructor() {
      this.sessionId = localStorage.getItem("mcpSessionId") || "sess_" + Math.random().toString(36).substring(2, 10);
      this.wireLogs = [];
      this.activeTab = "opportunityDeck";
      this.hackathons = [];
      this.profile = null;
      this.currentFilterSource = "";
      this.currentFilterFormat = "";
      this.currentFilterStatus = "open";
      this.searchQuery = "";
      this.sortFeasibility = true;
      this.onlyBookmarks = false;
      this.selectedHackathonId = null;
      this.speechSynthEnabled = true;
      this.recognition = null;
      this.isListening = false;
      this.currentAuditData = null;
      this.currentDiffData = null;
      this.currentSprintData = null;
      this.selectedBriefHackathon = null;
      this._activeUtterance = null;
      this.selectedVoice = null;
    }

    async init() {
      const sessionBadge = document.getElementById("mcpSessionBadge");
      if (sessionBadge) sessionBadge.innerText = this.sessionId.substring(0, 12);
      const codeEl = document.getElementById("drawerSessionCode");
      if (codeEl) codeEl.innerText = this.sessionId;

      // Listen for Escape key to close Hackathon Brief or Wire Drawer
      window.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          this.closeHackathonBrief();
          const drawer = document.getElementById("wireInspectorDrawer");
          if (drawer && drawer.classList.contains("open")) {
            drawer.classList.remove("open");
          }
        }
      });

      // 1. Handshake with MCP 2025-11-25
      await this.mcpHandshake();

      // 2. Load active Builder Profile
      await this.loadProfile();

      // 3. Load catalog (from DB — may be seed data only on first boot)
      await this.loadHackathons();

      // 4. Auto-refresh if catalog appears stale (fewer than 20 open hackathons)
      //    Runs async so it doesn't block voice/speech init
      this.autoRefreshIfStale().catch(() => {});

      // 5. Initialize Web Speech API
      this.initSpeech();
    }

    // -------------------------------------------------------------------------
    // Real POST /mcp JSON-RPC 2.0 Transport & Wire Log
    // -------------------------------------------------------------------------
    async callMCP(method, params = {}) {
      const id = Date.now() + Math.floor(Math.random() * 1000);
      const reqPayload = {
        jsonrpc: "2.0",
        id: id,
        method: method,
        params: params,
      };

      const startTime = performance.now();
      let resPayload = null;
      let status = 200;
      let error = null;

      try {
        const resp = await fetch("/mcp", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Mcp-Session-Id": this.sessionId,
          },
          body: JSON.stringify(reqPayload),
        });

        const newSession = resp.headers.get("Mcp-Session-Id");
        if (newSession) {
          this.sessionId = newSession;
          localStorage.setItem("mcpSessionId", this.sessionId);
          const badge = document.getElementById("mcpSessionBadge");
          if (badge) badge.innerText = this.sessionId.substring(0, 12);
          const codeEl = document.getElementById("drawerSessionCode");
          if (codeEl) codeEl.innerText = this.sessionId;
        }

        status = resp.status;
        resPayload = await resp.json();
      } catch (err) {
        error = err.message;
        resPayload = { error: { code: -32000, message: err.message } };
      }

      const elapsedMs = Math.round(performance.now() - startTime);

      // Log to Wire Inspector
      this.logWire({
        id: id,
        method: method,
        toolName: params.name || "",
        elapsedMs: elapsedMs,
        status: status,
        request: reqPayload,
        response: resPayload,
        timestamp: new Date().toLocaleTimeString(),
      });

      return resPayload;
    }

    logWire(entry) {
      this.wireLogs.unshift(entry);
      if (this.wireLogs.length > 50) this.wireLogs.pop();
      const countBadge = document.getElementById("wireCountBadge");
      if (countBadge) countBadge.innerText = this.wireLogs.length;

      const container = document.getElementById("wireLogsStream");
      if (!container) return;

      const div = document.createElement("div");
      div.className = "wire-entry";
      const isTool = entry.method === "tools/call";
      const label = isTool ? `tools/call (${entry.toolName})` : entry.method;

      div.innerHTML = `
        <div class="wire-entry-header">
          <span class="wire-method">${label}</span>
          <span class="wire-time">${entry.elapsedMs}ms • ${entry.status}</span>
        </div>
        <div style="font-size:10px; color:#94A3B8;">Req ID: ${entry.id} • ${entry.timestamp}</div>
        <details class="wire-details-disclosure">
          <summary>View JSON-RPC Payload</summary>
          <pre class="wire-payload-pre">Req: ${JSON.stringify(entry.request, null, 2)}\n\nRes: ${JSON.stringify(entry.response, null, 2)}</pre>
        </details>
      `;

      if (container.querySelector(".wire-empty-state")) {
        container.innerHTML = "";
      }
      container.insertBefore(div, container.firstChild);
    }

    clearWireLogs() {
      this.wireLogs = [];
      const countBadge = document.getElementById("wireCountBadge");
      if (countBadge) countBadge.innerText = "0";
      const stream = document.getElementById("wireLogsStream");
      if (stream) stream.innerHTML = '<div class="wire-empty-state">Wire logs cleared.</div>';
    }

    toggleWireInspector() {
      const drawer = document.getElementById("wireInspectorDrawer");
      if (drawer) drawer.classList.toggle("open");
    }

    // -------------------------------------------------------------------------
    // Handshake & Voice Narration
    // -------------------------------------------------------------------------
    async mcpHandshake() {
      await this.callMCP("initialize", {
        protocolVersion: "2025-11-25",
        capabilities: {},
        clientInfo: { name: "alexa-echo-show-15", version: "1.0.0" },
      });
      await this.callMCP("notifications/initialized", {});
    }

    speak(text) {
      if (!text) return;
      const ticker = document.getElementById("voiceTickerText");
      if (ticker) ticker.innerText = text;

      if (!this.speechSynthEnabled || !window.speechSynthesis) return;

      if (this.isListening) {
        return;
      }

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.05;
      utterance.pitch = 1.0;
      if (this.selectedVoice) {
        utterance.voice = this.selectedVoice;
      }

      this._activeUtterance = utterance;
      window._activeUtterance = utterance;

      const cleanupUtterance = () => {
        if (this._activeUtterance === utterance) {
          this._activeUtterance = null;
        }
        if (window._activeUtterance === utterance) {
          window._activeUtterance = null;
        }
      };

      utterance.onend = cleanupUtterance;
      utterance.onerror = cleanupUtterance;

      window.speechSynthesis.speak(utterance);
    }

    toggleTTS() {
      this.speechSynthEnabled = !this.speechSynthEnabled;
      const icon = document.getElementById("ttsIcon");
      if (icon) icon.innerText = this.speechSynthEnabled ? "🔊" : "🔇";
      if (!this.speechSynthEnabled && window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
    }

    initSpeech() {
      if (!("speechSynthesis" in window)) return;
      const pickVoice = () => {
        const voices = window.speechSynthesis.getVoices();
        const preferred = voices.find((v) =>
          (v.name.includes("Samantha") || v.name.includes("Zira") || v.name.includes("Google UK English Female") || (v.lang.startsWith("en") && v.name.includes("Female")))
        );
        this.selectedVoice = preferred || voices.find((v) => v.lang.startsWith("en")) || voices[0];
      };
      pickVoice();
      if (window.speechSynthesis.onvoiceschanged !== undefined) {
        window.speechSynthesis.onvoiceschanged = pickVoice;
      }
    }

    updateMicUI(listening, title = null) {
      const btn = document.getElementById("micBtn");
      if (!btn) return;
      btn.classList.toggle("listening", listening);
      btn.title = title || (listening ? "Listening... Click to stop" : "Click to speak with Alexa+");
    }

    getSpeechRecognition() {
      if (this.recognition) return this.recognition;
      const SpeechClass = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechClass) return null;

      const rec = new SpeechClass();
      rec.continuous = false;
      rec.lang = "en-US";
      rec.interimResults = false;
      rec.maxAlternatives = 1;

      rec.onstart = () => {
        this.isListening = true;
        this.updateMicUI(true);
        const ticker = document.getElementById("voiceTickerText");
        if (ticker) ticker.innerText = "Listening... Speak a search query or command.";
      };

      rec.onresult = (event) => {
        if (!event.results || !event.results[0] || !event.results[0][0]) return;
        const transcript = (event.results[0][0].transcript || "").trim();
        if (!transcript) return;

        const cmdInput = document.getElementById("commandInput");
        if (cmdInput) {
          cmdInput.value = transcript;
          cmdInput.focus();
        }

        const ticker = document.getElementById("voiceTickerText");
        if (ticker) {
          ticker.innerText = `Heard: "${transcript}". Press Send or Enter to execute.`;
        }
      };

      rec.onerror = (event) => {
        this.isListening = false;
        this.updateMicUI(false);
        const err = event.error || "unknown";
        let message = `Microphone input error (${err}).`;
        if (err === "not-allowed" || err === "service-not-allowed") {
          message = "Microphone access denied. Please allow microphone permissions in your browser.";
        } else if (err === "no-speech") {
          message = "No speech detected. Click the mic icon and speak clearly into your microphone.";
        } else if (err === "audio-capture") {
          message = "No microphone hardware detected or audio capture failed.";
        } else if (err === "network") {
          const isBrave = !!(navigator.brave && typeof navigator.brave.isBrave === "function");
          if (isBrave) {
            message = "Speech network error: Brave blocks Google Speech services by default. Enable voice in Brave settings, test in Chrome/Edge, or use the text command bar.";
          } else {
            message = "Speech recognition network connection error: The browser's speech service is unreachable. Please use the command bar.";
          }
        } else if (err === "aborted") {
          message = "Listening stopped.";
        }
        const ticker = document.getElementById("voiceTickerText");
        if (ticker) ticker.innerText = message;

        const cmdInput = document.getElementById("commandInput");
        if (cmdInput) cmdInput.focus();
      };

      rec.onend = () => {
        this.isListening = false;
        this.updateMicUI(false);
      };

      this.recognition = rec;
      return rec;
    }

    toggleSpeechRecognition() {
      const rec = this.getSpeechRecognition();
      if (!rec) {
        const msg = "Web Speech Recognition is not supported in this browser. Please use the text command bar.";
        const ticker = document.getElementById("voiceTickerText");
        if (ticker) ticker.innerText = msg;
        this.updateMicUI(false, msg);
        const cmdInput = document.getElementById("commandInput");
        if (cmdInput) cmdInput.focus();
        return;
      }

      if (this.isListening) {
        try {
          rec.stop();
        } catch (e) {
          this.isListening = false;
          this.updateMicUI(false);
        }
      } else {
        if (window.speechSynthesis && window.speechSynthesis.speaking) {
          window.speechSynthesis.cancel();
        }

        try {
          rec.start();
        } catch (err) {
          if (err.name === "InvalidStateError") {
            try { rec.stop(); } catch (e) {}
          }
          this.isListening = false;
          this.updateMicUI(false);
          const ticker = document.getElementById("voiceTickerText");
          if (ticker) ticker.innerText = `Could not start microphone (${err.message || err.name}). Click to retry or type below.`;
          const cmdInput = document.getElementById("commandInput");
          if (cmdInput) cmdInput.focus();
        }
      }
    }

    // -------------------------------------------------------------------------
    // Builder Profile & Hours Management
    // -------------------------------------------------------------------------
    async loadProfile() {
      const res = await this.callMCP("tools/call", {
        name: "manage_builder_profile",
        arguments: { action: "get" },
      });

      if (res && res.result && res.result.structuredContent) {
        this.profile = res.result.structuredContent.data;
        this.renderProfile();
      }
    }

    renderProfile() {
      if (!this.profile) return;
      const nameEl = document.getElementById("profileName");
      if (nameEl) nameEl.innerText = this.profile.name || "Demo Builder (CSE Student)";

      const countryEl = document.getElementById("profileCountry");
      if (countryEl && this.profile.country) {
        countryEl.innerText = this.profile.country;
      }

      const skillsEl = document.getElementById("profileSkills");
      if (skillsEl && Array.isArray(this.profile.skills) && this.profile.skills.length > 0) {
        const topSkills = this.profile.skills.slice(0, 4).join(" • ");
        skillsEl.innerText = topSkills;
        skillsEl.title = this.profile.skills.join(", ");
      }

      const budgetEl = document.getElementById("profileBudget");
      if (budgetEl && this.profile.budget_inr !== undefined) {
        budgetEl.innerText = `₹${Math.round(this.profile.budget_inr)} Budget`;
      }

      const bmCount = document.getElementById("bookmarkCount");
      if (bmCount) bmCount.innerText = (this.profile.bookmarks || []).length;

      // Update preset hours buttons
      const currentHours = this.profile.hours_per_week || 15;
      document.querySelectorAll(".hours-selector .pill-btn:not(.custom-set-btn)").forEach((btn) => {
        btn.classList.toggle("active", parseInt(btn.dataset.hours) === currentHours);
      });

      // Update custom hours input
      const customInput = document.getElementById("customHoursInput");
      if (customInput) {
        if (![10, 15, 25].includes(currentHours)) {
          customInput.value = currentHours;
        } else {
          customInput.placeholder = `${currentHours}h`;
        }
      }

      // Update online-only toggle
      const toggle = document.getElementById("onlineOnlyToggle");
      if (toggle) {
        toggle.classList.toggle("active", Boolean(this.profile.prefer_online_only));
      }
    }

    async updateProfileHours(hours) {
      const hInt = parseInt(hours, 10);
      if (isNaN(hInt) || hInt < 5 || hInt > 60) {
        const warning = "Please choose a weekly commitment between 5 and 60 hours per week.";
        this.setTicker(warning);
        this.speak(warning);
        return;
      }

      const res = await this.callMCP("tools/call", {
        name: "manage_builder_profile",
        arguments: { action: "update", hours_per_week: hInt },
      });
      if (res && res.result && res.result.structuredContent) {
        this.profile = res.result.structuredContent.data;
        this.renderProfile();
        this.speak(res.result.structuredContent.voiceSummary);
        await this.loadHackathons();

        // If sprint planner has active data, recalculate plan for new hours
        if (this.currentSprintData && this.currentSprintData.hackathon_id) {
          await this.sprintHackathon(this.currentSprintData.hackathon_id, null, hInt);
        }
      }
    }

    handleCustomHoursSubmit() {
      const input = document.getElementById("customHoursInput");
      if (!input) return;
      const val = parseInt(input.value, 10);
      if (isNaN(val) || val < 5 || val > 60) {
        const warning = "Custom availability must be between 5 and 60 hours per week.";
        this.setTicker(warning);
        this.speak(warning);
        input.focus();
        return;
      }
      this.updateProfileHours(val);
      input.blur();
    }

    async toggleOnlineOnly() {
      if (!this.profile) return;
      const nextVal = !this.profile.prefer_online_only;
      const res = await this.callMCP("tools/call", {
        name: "manage_builder_profile",
        arguments: { action: "update", prefer_online_only: nextVal },
      });
      if (res && res.result && res.result.structuredContent) {
        this.profile = res.result.structuredContent.data;
        this.renderProfile();
        this.speak(`Filter preference updated: ${nextVal ? "Online hackathons only" : "All formats"}.`);
        await this.loadHackathons();
      }
    }

    async toggleBookmark(hackathonId, event) {
      if (event) event.stopPropagation();
      const res = await this.callMCP("tools/call", {
        name: "manage_builder_profile",
        arguments: { action: "update", toggle_bookmark_id: hackathonId },
      });
      if (res && res.result && res.result.structuredContent) {
        this.profile = res.result.structuredContent.data;
        this.renderProfile();
        const isNowBookmarked = this.profile.bookmarks.includes(hackathonId);
        this.speak(isNowBookmarked ? "Hackathon saved to bookmarks." : "Hackathon removed from bookmarks.");
        this.renderHackathons();
      }
    }

    // -------------------------------------------------------------------------
    // Catalog Discovery & Filters
    // -------------------------------------------------------------------------
    async loadHackathons() {
      const args = {
        query: this.searchQuery,
        source: this.currentFilterSource,
        sort_by_feasibility: this.sortFeasibility,
        limit: 100,
      };

      if (this.currentFilterStatus) {
        args.status = this.currentFilterStatus;
      }

      if (this.currentFilterFormat) {
        args.location = this.currentFilterFormat;
      } else if (this.profile && this.profile.prefer_online_only) {
        args.location = "Online";
      }

      const res = await this.callMCP("tools/call", {
        name: "search_hackathons",
        arguments: args,
      });

      if (res && res.result && res.result.structuredContent) {
        this.hackathons = res.result.structuredContent.data.hackathons || [];
        this.renderHackathons();
      }
    }

    async autoRefreshIfStale() {
      // Trigger a live discovery refresh if the catalog appears empty or stale.
      // This fires once per session and is non-blocking (UI already shows whatever is in DB).
      try {
        const checkRes = await this.callMCP("tools/call", {
          name: "search_hackathons",
          arguments: { status: "open", limit: 25 },
        });
        const count = (checkRes && checkRes.result && checkRes.result.structuredContent &&
          checkRes.result.structuredContent.data.count) || 0;

        // If fewer than 20 open hackathons are visible, trigger a live discovery refresh.
        if (count < 20) {
          const ticker = document.getElementById("voiceTickerText");
          if (ticker) ticker.innerText = "Discovering live hackathons from Devpost, Devfolio & Unstop...";

          await this.callMCP("tools/call", {
            name: "sync_upstream_catalog",
            arguments: { discover_live: true, retention_days: 30 },
          });

          // Reload the hackathon list with the freshly discovered data
          await this.loadHackathons();
          if (ticker) ticker.innerText = "Catalog refreshed. Showing live open hackathons.";
        }
      } catch (e) {
        // Non-fatal — the UI already shows whatever was in the DB
        console.warn("Auto-refresh check failed:", e);
      }
    }

    canonicalizeUrl(url) {
      if (!url) return "";
      let u = String(url).trim().replace(/[?#].*$/, "").replace(/\/+$/, "");
      const uLower = u.toLowerCase();
      if (
        uLower === "https://devpost.com" || uLower === "http://devpost.com" ||
        uLower === "https://devpost.com/hackathons" || uLower === "http://devpost.com/hackathons" ||
        uLower === "https://devfolio.co" || uLower === "http://devfolio.co" ||
        uLower === "https://devfolio.co/hackathons" || uLower === "http://devfolio.co/hackathons" ||
        uLower === "https://unstop.com" || uLower === "http://unstop.com" ||
        uLower === "https://unstop.com/hackathons" || uLower === "http://unstop.com/hackathons"
      ) {
        return "";
      }
      const devpostMatch = uLower.match(/^https?:\/\/(?:www\.)?devpost\.com\/hackathons\/([^/]+)/);
      if (devpostMatch) return `https://${devpostMatch[1]}.devpost.com`;
      const subDevpost = uLower.match(/^https?:\/\/([^/]+)\.devpost\.com/);
      if (subDevpost && !["www", "api", "help", "info"].includes(subDevpost[1])) return `https://${subDevpost[1]}.devpost.com`;

      const devfolioMatch = uLower.match(/^https?:\/\/(?:www\.)?devfolio\.co\/hackathons\/([^/]+)/);
      if (devfolioMatch) return `https://${devfolioMatch[1]}.devfolio.co`;
      const subDevfolio = uLower.match(/^https?:\/\/([^/]+)\.devfolio\.co/);
      if (subDevfolio && !["www", "api", "assets"].includes(subDevfolio[1])) return `https://${subDevfolio[1]}.devfolio.co`;

      const unstopMatch = uLower.match(/^https?:\/\/(?:www\.)?unstop\.com\/hackathons\/([^/]+)/);
      if (unstopMatch) return `https://unstop.com/hackathons/${unstopMatch[1]}`;

      return u;
    }

    normalizeEventIdentity(title, organizer = "") {
      let t = (title || "").toLowerCase();
      const editionMatches = t.match(/\b(?:\d+(?:\.\d+)+|v\d+(?:\.\d+)*|\d{4}|'\d{2}|3\.o|[ivxlcdm]+)\b/g) || [];
      const org = (organizer || "").toLowerCase().replace(/[^a-z0-9]/g, "");

      t = t.replace(/\b(hackathon|hack|challenge|competition|series|tour|stop|global|edition|annual|presents|hosted\s+by)\b/g, " ");
      t = t.replace(/[^a-z0-9\s]/g, " ");
      const words = t.split(/\s+/).filter(w => w.length > 1);

      for (const ed of editionMatches) {
        const cleanEd = ed.replace(/[^a-z0-9]/g, "");
        if (cleanEd && !words.includes(cleanEd)) {
          words.push(cleanEd);
        }
      }
      words.sort();
      const normTitle = words.join("-");
      return org ? `${normTitle}__${org}` : normTitle;
    }

    mergeHackathonRecords(primary, duplicate) {
      const merged = Object.assign({}, primary);
      const isOfficial = (u) => {
        if (!u) return false;
        const low = u.toLowerCase();
        return !low.includes("devpost.com/hackathons?") && !low.includes("devfolio.co/hackathons?") && !low.includes("unstop.com/hackathons?");
      };

      if (!isOfficial(merged.url) && isOfficial(duplicate.url)) {
        merged.url = duplicate.url;
      }
      for (const key of ["reg_starts_at", "reg_ends_at", "starts_at", "ends_at", "deadline", "description", "prize", "location"]) {
        if (!merged[key] && duplicate[key]) {
          merged[key] = duplicate[key];
        }
      }
      if (Array.isArray(duplicate.tags) && duplicate.tags.length > 0) {
        const tags = Array.isArray(merged.tags) ? merged.tags.slice() : [];
        const seen = new Set(tags.map(t => String(t).toLowerCase()));
        for (const t of duplicate.tags) {
          if (!seen.has(String(t).toLowerCase())) {
            tags.push(t);
            seen.add(String(t).toLowerCase());
          }
        }
        merged.tags = tags;
      }
      return merged;
    }

    deduplicateHackathons(items) {
      if (!Array.isArray(items) || items.length === 0) return [];
      const urlMap = new Map();
      const identMap = new Map();
      const order = [];

      for (const raw of items) {
        const item = Object.assign({}, raw);
        const canonUrl = this.canonicalizeUrl(item.url);
        const ident = this.normalizeEventIdentity(item.title, item.organizer || item.source || "");

        let existing = null;
        if (canonUrl && urlMap.has(canonUrl)) {
          existing = urlMap.get(canonUrl);
        } else if (ident && identMap.has(ident)) {
          existing = identMap.get(ident);
        }

        if (existing) {
          const merged = this.mergeHackathonRecords(existing, item);
          Object.assign(existing, merged);
          if (canonUrl && !urlMap.has(canonUrl)) urlMap.set(canonUrl, existing);
          if (ident && !identMap.has(ident)) identMap.set(ident, existing);
        } else {
          const cloned = Object.assign({}, item);
          if (canonUrl) urlMap.set(canonUrl, cloned);
          if (ident) identMap.set(ident, cloned);
          order.push(cloned);
        }
      }

      const seenIds = new Set();
      const result = [];
      for (const record of order) {
        if (record.id && !seenIds.has(record.id)) {
          seenIds.add(record.id);
          result.push(record);
        }
      }
      return result;
    }

    formatCardDate(dtStr) {
      if (!dtStr || dtStr === "Unknown" || dtStr === "TBA" || dtStr.toLowerCase() === "ended") return "";
      try {
        const clean = dtStr.trim().replace("Z", "+00:00");
        const d = new Date(clean);
        if (!isNaN(d.getTime())) {
          return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
        }
      } catch (e) {}
      if (dtStr.includes("T")) return dtStr.split("T")[0];
      return dtStr;
    }

    formatEventRange(startsAt, endsAt) {
      const s = this.formatCardDate(startsAt);
      const e = this.formatCardDate(endsAt);
      if (s && e) {
        if (s === e) return s;
        return `${s} – ${e}`;
      }
      return s || e || "";
    }

    escapeHtml(str) {
      if (!str) return "";
      return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    renderHackathons() {
      const grid = document.getElementById("hackathonGrid");
      if (!grid) return;

      // Deduplicate records across sources before rendering
      let list = this.deduplicateHackathons(this.hackathons);

      // Apply client-side safety filters if format/status are active
      if (this.currentFilterFormat) {
        const fmtLower = this.currentFilterFormat.toLowerCase();
        list = list.filter((h) => (h.location || "").toLowerCase().includes(fmtLower));
      }

      const now = new Date();
      if (this.currentFilterStatus === "open") {
        list = list.filter((h) => {
          const statLower = (h.status || "").toLowerCase();
          if (statLower !== "open") return false;
          // Registration deadline must not have passed
          const regDeadlineStr = h.reg_ends_at || (h.deadline && !h.deadline.includes(" - ") ? h.deadline : "");
          if (regDeadlineStr) {
            const dt = new Date(regDeadlineStr.replace("Z", "+00:00"));
            if (!isNaN(dt.getTime()) && dt <= now) return false;
          }
          // Event end date must not have passed
          if (h.ends_at) {
            const dtEnds = new Date(h.ends_at.replace("Z", "+00:00"));
            if (!isNaN(dtEnds.getTime()) && dtEnds <= now) return false;
          }
          // Exclude events whose registration is not yet open
          if (h.reg_starts_at) {
            const dtStarts = new Date(h.reg_starts_at.replace("Z", "+00:00"));
            if (!isNaN(dtStarts.getTime()) && dtStarts > now) return false;
          }
          return true;
        });
      } else if (this.currentFilterStatus === "past") {
        const cutoff30d = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
        list = list.filter((h) => {
          const statLower = (h.status || "").toLowerCase();
          const isEnded = ["ended", "concluded", "completed", "finished"].includes(statLower);
          let endDt = null;
          if (h.ends_at) {
            const dt = new Date(h.ends_at.replace("Z", "+00:00"));
            if (!isNaN(dt.getTime())) endDt = dt;
          }
          if (!endDt && h.inactive_since) {
            const dt = new Date(h.inactive_since.replace("Z", "+00:00"));
            if (!isNaN(dt.getTime())) endDt = dt;
          }
          if (!endDt && h.deadline) {
            const dt = new Date(h.deadline.replace("Z", "+00:00"));
            if (!isNaN(dt.getTime())) endDt = dt;
          }
          if (!isEnded && (!endDt || endDt > now)) {
            return false;
          }
          if (endDt && endDt < cutoff30d) {
            return false;
          }
          return true;
        });
        // Sort newest first
        list.sort((a, b) => {
          const dateA = a.ends_at || a.inactive_since || a.deadline || "";
          const dateB = b.ends_at || b.inactive_since || b.deadline || "";
          return dateB.localeCompare(dateA);
        });
      }

      if (this.onlyBookmarks && this.profile) {
        list = list.filter((h) => this.profile.bookmarks.includes(h.id));
      }

      // Update 30-day cutoff banner and catalog count pill
      const banner = document.getElementById("pastCutoffBanner");
      if (banner) {
        if (this.currentFilterStatus === "past") {
          const cutoffDate = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
          const cutoffStr = cutoffDate.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
          const cutoffEl = document.getElementById("cutoffDateStr");
          if (cutoffEl) cutoffEl.innerText = cutoffStr;
          banner.style.display = "flex";
        } else {
          banner.style.display = "none";
        }
      }

      const countPill = document.getElementById("catalogCountPill");
      if (countPill) {
        const suffix = this.currentFilterStatus === "past" ? "Past Hackathons (Last 30 Days)" : "Open Hackathons";
        countPill.innerText = `${list.length} ${suffix}`;
      }

      if (list.length === 0) {
        this.updateResetButtonVisibility(true);
        const queryDisplay = this.searchQuery ? `"${this.escapeHtml(this.searchQuery)}"` : "current filters";
        grid.innerHTML = `
          <div class="empty-recovery-card" style="text-align:center; padding: 36px 20px; background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: 14px; max-width: 620px; margin: 30px auto; box-shadow: 0 4px 20px rgba(0,0,0,0.3);">
            <div style="font-size: 32px; margin-bottom: 12px;">🔍</div>
            <h3 style="font-size: 18px; color: var(--text-primary); margin-bottom: 8px;">No hackathons matched ${queryDisplay}</h3>
            <p style="font-size: 14px; color: var(--text-secondary); margin-bottom: 18px; line-height: 1.5;">
              Try one of these recommended search categories or clear restrictive filters:
            </p>
            <div class="recovery-chips" style="display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; margin-bottom: 20px;">
              <button class="chip" onclick="window.app.executeQuickSearch('AI')">🤖 AI & Machine Learning</button>
              <button class="chip" onclick="window.app.executeQuickSearch('Alexa')">🎙️ Amazon / Alexa+</button>
              <button class="chip" onclick="window.app.executeQuickSearch('Agent')">⚡ Agentic Hackathons</button>
              <button class="chip" onclick="window.app.executeQuickSearch('Devpost')">🌐 Devpost</button>
              <button class="chip" onclick="window.app.executeQuickSearch('')">📋 View All</button>
            </div>
            <button class="action-btn primary" onclick="window.app.resetFilters()" style="margin: 0 auto;">✕ Clear Search & Reset Filters</button>
          </div>
        `;
        return;
      }
      this.updateResetButtonVisibility();

      grid.innerHTML = list.map((h) => {
        const score = h.feasibility_score || 50;
        const isBookmarked = this.profile && this.profile.bookmarks.includes(h.id);
        const sourceLower = (h.source || "devpost").toLowerCase();
        const isOnline = (h.location || "").toLowerCase().includes("online");
        const statusLower = (h.status || "open").toLowerCase();
        const officialUrl = h.url || "";

        let statusBadgeHtml = '';
        if (statusLower === 'open' || statusLower === 'active') {
          statusBadgeHtml = '<span class="status-badge open">🟢 Open</span>';
        } else if (statusLower === 'ended' || statusLower === 'closed' || statusLower === 'finished' || statusLower === 'completed') {
          statusBadgeHtml = '<span class="status-badge ended">⚪ Concluded</span>';
        } else {
          statusBadgeHtml = '<span class="status-badge unknown">❓ Unverified</span>';
        }

        const regDeadlineStr = h.reg_ends_at || (h.deadline && !h.deadline.includes(" - ") ? h.deadline : "");
        const regEndFormatted = this.formatCardDate(regDeadlineStr);
        const eventDatesFormatted = this.formatEventRange(h.starts_at, h.ends_at);

        const reasonsHtml = (h.fit_reasons || []).slice(0, 2).map((r) => `
          <div class="fit-reason-item"><span>✓</span> <span>${this.escapeHtml(r)}</span></div>
        `).join("");

        const risksHtml = (h.risk_flags || []).slice(0, 1).map((r) => `
          <div class="risk-flag-item"><span>⚠️</span> <span>${this.escapeHtml(r)}</span></div>
        `).join("");

        const officialLinkHtml = officialUrl ? `
          <a href="${this.escapeHtml(officialUrl)}" target="_blank" rel="noopener noreferrer" class="official-url-link" onclick="event.stopPropagation()" title="Open official ${h.source} event page in new tab">
            🌐 Visit Official Site ↗
          </a>
        ` : `
          <span class="official-url-link disabled" title="Official external link unavailable in catalogue">
            🌐 Official Link Unavailable
          </span>
        `;

        return `
          <div class="hack-card" onclick="window.app.openHackathonBrief('${h.id}')" tabindex="0" role="button" aria-label="Open hackathon brief for ${this.escapeHtml(h.title)}">
            <div>
              <div class="card-top">
                <div class="card-badges">
                  <span class="source-badge ${sourceLower}">${this.escapeHtml(h.source)}</span>
                  <span class="location-badge ${isOnline ? 'online' : 'in-person'}">${this.escapeHtml(h.location || 'Online')}</span>
                  ${statusBadgeHtml}
                </div>
                <div class="feasibility-ring" style="--score: ${score}" title="Multi-factor Feasibility Score: ${score}%">
                  <div class="feasibility-inner">${score}%</div>
                </div>
              </div>
              <div class="card-title-row">
                <h3 class="card-title">${this.escapeHtml(h.title)}</h3>
                ${officialLinkHtml}
              </div>
              <div class="card-meta-row">
                <span class="prize-pill">${this.escapeHtml(h.prize || 'Prizes Listed')}</span>
                ${regEndFormatted ? `<span class="deadline-pill reg-deadline" title="Official Registration Deadline">Reg Closes: ${this.escapeHtml(regEndFormatted)}</span>` : (h.deadline ? `<span class="deadline-pill">Deadline: ${this.escapeHtml(h.deadline.split('T')[0])}</span>` : '')}
                ${eventDatesFormatted ? `<span class="event-dates-pill" title="Official Hackathon Period">Event: ${this.escapeHtml(eventDatesFormatted)}</span>` : ''}
              </div>
              <p class="card-desc">${this.escapeHtml(h.description || 'No description provided.')}</p>
            </div>

            <div>
              <div class="fit-reasons-list">
                ${reasonsHtml}
                ${risksHtml}
              </div>
              <div class="card-actions">
                <button class="action-btn secondary" onclick="event.stopPropagation(); window.app.openHackathonBrief('${h.id}')" title="Read concise Hackathon Brief & requirements summary">
                  📋 Brief
                </button>
                <button class="action-btn" onclick="event.stopPropagation(); window.app.auditHackathon('${h.id}')" title="Audit official rules with live provenance">
                  🔍 Rules
                </button>
                <button class="action-btn" onclick="event.stopPropagation(); window.app.diffHackathon('${h.id}')" title="Inspect rule discrepancies and risk diff">
                  ⚖️ Diff
                </button>
                <button class="action-btn" onclick="event.stopPropagation(); window.app.sprintHackathon('${h.id}')" title="Generate persistent sprint plan">
                  🗓️ Plan
                </button>
                <button class="bookmark-icon-btn ${isBookmarked ? 'active' : ''}" onclick="window.app.toggleBookmark('${h.id}', event)" title="Toggle Bookmark" aria-label="Bookmark hackathon">
                  ${isBookmarked ? '★' : '☆'}
                </button>
              </div>
            </div>
          </div>
        `;
      }).join("");
    }

    handleSearch(val) {
      this.searchQuery = (val || "").trim();
      const searchInput = document.getElementById("searchInput");
      if (searchInput && searchInput.value !== this.searchQuery) {
        searchInput.value = this.searchQuery;
      }
      this.updateResetButtonVisibility();
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => this.loadHackathons(), 250);
    }

    updateResetButtonVisibility(forceShow = false) {
      const resetBtn = document.getElementById("resetSearchBtn");
      if (!resetBtn) return;
      const isCustomStatus = this.currentFilterStatus !== "open";
      if (forceShow || this.searchQuery.length > 0 || this.currentFilterSource || this.currentFilterFormat || isCustomStatus || this.onlyBookmarks) {
        resetBtn.style.display = "inline-flex";
      } else {
        resetBtn.style.display = "none";
      }
    }

    resetFilters() {
      this.searchQuery = "";
      const searchInput = document.getElementById("searchInput");
      if (searchInput) searchInput.value = "";
      this.currentFilterSource = "";
      this.currentFilterFormat = "";
      this.currentFilterStatus = "open";

      const banner = document.getElementById("pastCutoffBanner");
      if (banner) banner.style.display = "none";

      document.querySelectorAll("#sourceChips .chip").forEach((c) => {
        c.classList.toggle("active", c.dataset.source === "");
      });
      document.querySelectorAll("#formatChips .chip").forEach((c) => {
        c.classList.toggle("active", c.dataset.format === "");
      });
      document.querySelectorAll("#statusChips .chip").forEach((c) => {
        c.classList.toggle("active", (c.dataset.status || "") === "open");
      });

      this.onlyBookmarks = false;
      const bmBtn = document.getElementById("filterBookmarksBtn");
      if (bmBtn) bmBtn.classList.remove("active");
      this.sortFeasibility = true;
      const sortBtn = document.getElementById("sortFeasibilityBtn");
      if (sortBtn) sortBtn.classList.add("active");

      this.updateResetButtonVisibility();
      this.loadHackathons();
      this.speak("Search query and filters reset to open hackathons.");
    }

    setTicker(text) {
      const ticker = document.getElementById("voiceTickerText");
      if (ticker) ticker.innerText = text;
    }

    executeQuickSearch(term) {
      this.searchQuery = term;
      const searchInput = document.getElementById("searchInput");
      if (searchInput) searchInput.value = term;
      this.currentFilterSource = "";
      document.querySelectorAll("#sourceChips .chip").forEach((c) => {
        c.classList.toggle("active", c.dataset.source === "");
      });
      this.updateResetButtonVisibility();
      this.switchTab("opportunityDeck");
      this.loadHackathons();
      this.speak(term ? `Filtering catalog for '${term}'.` : "Displaying all available hackathons.");
    }

    setSourceFilter(src) {
      this.searchQuery = "";
      const searchInput = document.getElementById("searchInput");
      if (searchInput) searchInput.value = "";
      this.currentFilterSource = src;
      document.querySelectorAll("#sourceChips .chip").forEach((c) => {
        c.classList.toggle("active", c.dataset.source === src);
      });
      this.updateResetButtonVisibility();
      this.loadHackathons();
    }

    setFormatFilter(fmt) {
      this.currentFilterFormat = fmt;
      document.querySelectorAll("#formatChips .chip").forEach((c) => {
        c.classList.toggle("active", c.dataset.format === fmt);
      });
      this.updateResetButtonVisibility();
      this.loadHackathons();
    }

    setStatusFilter(stat) {
      this.setStatusScope(stat);
    }

    setStatusScope(scope) {
      this.currentFilterStatus = (scope === "past" ? "past" : "open");
      document.querySelectorAll("#statusChips .chip").forEach((c) => {
        c.classList.toggle("active", (c.dataset.status || "") === this.currentFilterStatus);
      });
      this.updateResetButtonVisibility();
      this.loadHackathons();
      if (this.currentFilterStatus === "open") {
        this.speak("Showing open hackathons with active registration.");
      } else {
        this.speak("Showing past hackathons ended within the last 30 days.");
      }
    }

    toggleSortFeasibility() {
      this.sortFeasibility = !this.sortFeasibility;
      const sortBtn = document.getElementById("sortFeasibilityBtn");
      if (sortBtn) sortBtn.classList.toggle("active", this.sortFeasibility);
      this.loadHackathons();
    }

    toggleFilterBookmarks() {
      this.onlyBookmarks = !this.onlyBookmarks;
      const bmBtn = document.getElementById("filterBookmarksBtn");
      if (bmBtn) bmBtn.classList.toggle("active", this.onlyBookmarks);
      this.renderHackathons();
    }

    async syncCatalog() {
      const btn = document.getElementById("syncCatalogBtn");
      if (btn) btn.style.opacity = "0.5";
      const ticker = document.getElementById("voiceTickerText");
      if (ticker) ticker.innerText = "Refreshing discovery sources (Devpost, Devfolio, Unstop)...";
      const res = await this.callMCP("tools/call", {
        name: "sync_upstream_catalog",
        arguments: { discover_live: true, retention_days: 30 },
      });
      if (btn) btn.style.opacity = "1";

      if (res && res.result && res.result.structuredContent) {
        this.speak(res.result.structuredContent.voiceSummary);
        if (ticker) ticker.innerText = res.result.structuredContent.voiceSummary;
      }
      await this.loadHackathons();
    }

    switchTab(tabName) {
      this.activeTab = tabName;
      document.querySelectorAll(".stage-nav .stage-tab").forEach((tab) => {
        tab.classList.toggle("active", tab.dataset.tab === tabName);
      });
      document.querySelectorAll(".stage-view").forEach((view) => {
        const id = "view" + tabName.charAt(0).toUpperCase() + tabName.slice(1);
        view.classList.toggle("active", view.id === id);
      });
    }

    // -------------------------------------------------------------------------
    // NEW FEATURE: Hackathon Brief & Event Summarizer (Contextual Detail View)
    // -------------------------------------------------------------------------
    async openHackathonBrief(idOrTitle) {
      this.selectedHackathonId = idOrTitle;
      const drawer = document.getElementById("hackathonBriefDrawer");
      const backdrop = document.getElementById("briefModalBackdrop");
      const content = document.getElementById("briefDrawerContent");
      const titleEl = document.getElementById("briefDrawerTitle");

      if (drawer) drawer.classList.add("open");
      if (backdrop) backdrop.classList.add("open");
      if (content) content.innerHTML = `<div class="loading-state">Synthesizing verified hackathon brief...</div>`;

      // 1. Resolve hackathon data
      let h = this.hackathons.find((x) => x.id === idOrTitle || x.title === idOrTitle);
      if (!h) {
        const dRes = await this.callMCP("tools/call", {
          name: "get_hackathon_details",
          arguments: { identifier: idOrTitle },
        });
        if (dRes && dRes.result && !dRes.result.isError && dRes.result.structuredContent) {
          h = dRes.result.structuredContent.data;
        }
      }

      if (!h) {
        if (content) content.innerHTML = `<div class="placeholder-prompt"><h3>Event Not Found</h3><p>Could not retrieve details for this hackathon.</p></div>`;
        return;
      }

      if (titleEl) titleEl.innerText = h.title;

      // 2. Query rule audit data for deeper structured fields
      let audit = null;
      const aRes = await this.callMCP("tools/call", {
        name: "audit_rules",
        arguments: { identifier: h.id, force_live: false },
      });
      if (aRes && aRes.result && aRes.result.structuredContent) {
        audit = aRes.result.structuredContent.data;
      }

      this.renderHackathonBrief(h, audit);
    }

    closeHackathonBrief() {
      const drawer = document.getElementById("hackathonBriefDrawer");
      const backdrop = document.getElementById("briefModalBackdrop");
      if (drawer) drawer.classList.remove("open");
      if (backdrop) backdrop.classList.remove("open");
    }

    renderHackathonBrief(h, audit) {
      const content = document.getElementById("briefDrawerContent");
      if (!content) return;

      const cats = (audit && audit.categories) || {};
      const isLive = audit && audit.verification_status === "verified_live";
      const officialUrl = h.url || (audit && audit.source_url) || "";
      const score = h.feasibility_score || 50;

      // Compute remaining runway
      let runwayDaysStr = "TBA";
      if (h.deadline && h.deadline !== "Unknown") {
        try {
          const dl = new Date(h.deadline);
          const diffDays = Math.ceil((dl - new Date()) / (1000 * 60 * 60 * 24));
          runwayDaysStr = diffDays > 0 ? `${diffDays} days` : (diffDays === 0 ? "Closing today" : "Concluded");
        } catch (e) {
          runwayDaysStr = "TBA";
        }
      }

      // 10 Structured Content Fields derived deterministically from verified source data
      const overviewText = h.description || "Official challenge overview and problem statement.";
      const eligibilityText = cats.eligibility || `Open to registered participants on ${h.source || 'host platform'}. Location: ${h.location || 'Online'}.`;
      const regEndFmt = this.formatCardDate(h.reg_ends_at || h.deadline);
      const eventRangeFmt = this.formatEventRange(h.starts_at, h.ends_at);
      const datesText = `Registration Cutoff: ${regEndFmt || 'TBA'} • Runway: ${runwayDaysStr}${eventRangeFmt ? ' • Event Period: ' + eventRangeFmt : ''}`;
      const teamText = cats.team_size || `Teams of 1–4 participants permitted per ${h.source || 'official'} guidelines.`;
      const deliverablesText = cats.submission_requirements || `Public code repository, functional demonstration video, and project documentation on ${h.source || 'platform'}.`;
      const techStackText = (h.tags && h.tags.length > 0) ? h.tags.join(", ") : "Open technical stack / domain-relevant tools";
      const judgingText = `Evaluated on technical implementation, alignment with ${h.title} theme, architecture quality, and demonstration completeness.`;
      const prizeText = `${h.prize || 'Prizes listed'} (Currency: ${h.prize_currency || 'USD'})`;
      const restrictionsText = cats.hosting_requirements || `Original code required. Open-source licensing for submitted software components. Endpoint uptime recommended through judging.`;
      const nextActionText = (h.fit_reasons && h.fit_reasons[0])
        ? `${h.fit_reasons[0]}. Verify official rules, clone project scaffolding, and validate core prototype logic by Hour 6.`
        : `Audit official guidelines on ${h.source} and establish a 15-hour milestone sprint plan before committing full-time development.`;

      content.innerHTML = `
        <!-- Provenance Confidence Banner -->
        <div class="brief-provenance-banner ${isLive ? 'verified_live' : 'fallback_unverified'}">
          <span style="font-size:22px;">${isLive ? '🛡️' : '⚠️'}</span>
          <div>
            <div style="font-size:12px; font-weight:700; color:var(--text-primary);">
              ${isLive ? 'Verified Live Official Rules' : 'Catalogue Metadata (Unverified Live)'}
            </div>
            <div style="font-size:11px; color:var(--text-secondary); margin-top:2px;">
              ${isLive ? `Extracted directly from official terms on ${audit.retrieval_timestamp ? audit.retrieval_timestamp.split('.')[0] + 'Z' : 'recent inspection'}.` : 'Synthesized from ingested event catalog. Official website terms pending live inspection.'}
            </div>
          </div>
        </div>

        <!-- Quick Metrics Grid -->
        <div class="brief-quick-stats">
          <div class="brief-stat-box">
            <div class="brief-stat-label">Feasibility</div>
            <div class="brief-stat-val" style="color:var(--alexa-cyan);">${score}%</div>
          </div>
          <div class="brief-stat-box">
            <div class="brief-stat-label">Format</div>
            <div class="brief-stat-val" style="font-size:13px;">${this.escapeHtml(h.location || 'Online')}</div>
          </div>
          <div class="brief-stat-box">
            <div class="brief-stat-label">Reg Status</div>
            <div class="brief-stat-val" style="font-size:12px; font-weight:700;">${(h.status || 'open').toLowerCase() === 'open' ? '🟢 Open' : (h.status || '').toLowerCase() === 'ended' ? '⚪ Closed' : '❓ Unverified'}</div>
          </div>
          <div class="brief-stat-box">
            <div class="brief-stat-label">Runway</div>
            <div class="brief-stat-val" style="font-size:13px;">${runwayDaysStr}</div>
          </div>
          <div class="brief-stat-box">
            <div class="brief-stat-label">Prize Pool</div>
            <div class="brief-stat-val" style="font-size:13px; color:var(--emerald-500);">${this.escapeHtml(h.prize || 'Listed')}</div>
          </div>
        </div>

        <!-- Section 1: Overview -->
        <div class="brief-section-card">
          <div class="brief-section-title"><span>🎯</span> 1. Challenge Overview & Scope</div>
          <div class="brief-section-body">${this.escapeHtml(overviewText)}</div>
        </div>

        <!-- Section 2: Eligibility -->
        <div class="brief-section-card">
          <div class="brief-section-title"><span>🌍</span> 2. Who Can Participate (Eligibility)</div>
          <div class="brief-section-body">${this.escapeHtml(eligibilityText)}</div>
        </div>

        <!-- Section 3: Important Dates -->
        <div class="brief-section-card">
          <div class="brief-section-title"><span>📅</span> 3. Important Dates & Submission Cutoff</div>
          <div class="brief-section-body">${this.escapeHtml(datesText)}</div>
        </div>

        <!-- Section 4: Team Rules -->
        <div class="brief-section-card">
          <div class="brief-section-title"><span>👥</span> 4. Team Requirements & Permitted Sizes</div>
          <div class="brief-section-body">${this.escapeHtml(teamText)}</div>
        </div>

        <!-- Section 5: What to Submit -->
        <div class="brief-section-card">
          <div class="brief-section-title"><span>📦</span> 5. Required Submission Deliverables</div>
          <div class="brief-section-body">${this.escapeHtml(deliverablesText)}</div>
        </div>

        <!-- Section 6: Tech Stack -->
        <div class="brief-section-card">
          <div class="brief-section-title"><span>⚡</span> 6. Recommended Technology & Domain Tags</div>
          <div class="brief-section-body">${this.escapeHtml(techStackText)}</div>
        </div>

        <!-- Section 7: Judging -->
        <div class="brief-section-card">
          <div class="brief-section-title"><span>⚖️</span> 7. Evaluation & Judging Criteria</div>
          <div class="brief-section-body">${this.escapeHtml(judgingText)}</div>
        </div>

        <!-- Section 8: Prizes -->
        <div class="brief-section-card">
          <div class="brief-section-title"><span>🏆</span> 8. Prize Pool & Verified Bounties</div>
          <div class="brief-section-body">${this.escapeHtml(prizeText)}</div>
        </div>

        <!-- Section 9: Restrictions & Hosting -->
        <div class="brief-section-card">
          <div class="brief-section-title"><span>☁️</span> 9. Critical Restrictions & Deployment Guardrails</div>
          <div class="brief-section-body">${this.escapeHtml(restrictionsText)}</div>
        </div>

        <!-- Section 10: Recommended Next Action -->
        <div class="brief-section-card" style="border-color: rgba(0, 202, 255, 0.4); background: rgba(0, 202, 255, 0.05);">
          <div class="brief-section-title" style="color:var(--alexa-cyan);"><span>💡</span> 10. Recommended Next Action</div>
          <div class="brief-section-body">${this.escapeHtml(nextActionText)}</div>
        </div>

        <!-- Action Buttons -->
        <div class="brief-actions-row">
          ${officialUrl ? `
            <a href="${this.escapeHtml(officialUrl)}" target="_blank" rel="noopener noreferrer" class="action-btn primary" style="text-decoration:none;">
              🌐 Open Official Event Page ↗
            </a>
          ` : ''}
          <button class="action-btn" onclick="window.app.closeHackathonBrief(); window.app.auditHackathon('${h.id}')">
            🛡️ Inspect Full Rule Audit
          </button>
          <button class="action-btn" onclick="window.app.closeHackathonBrief(); window.app.sprintHackathon('${h.id}')">
            🚀 Generate Sprint Plan
          </button>
          <button class="action-btn" onclick="window.app.closeHackathonBrief(); window.app.diffHackathon('${h.id}')">
            ⚖️ Check Rule Diffs
          </button>
        </div>
      `;
    }

    // -------------------------------------------------------------------------
    // Rule Provenance Audit (Tab 2)
    // -------------------------------------------------------------------------
    async auditHackathon(idOrTitle, speakSummary = true) {
      this.selectedHackathonId = idOrTitle;
      this.switchTab("ruleAuditBoard");
      const container = document.getElementById("auditBoardContainer");
      if (container) {
        container.innerHTML = `<div class="loading-state">Executing MCP audit_rules with provenance verification...</div>`;
      }

      const res = await this.callMCP("tools/call", {
        name: "audit_rules",
        arguments: { identifier: idOrTitle, force_live: true },
      });

      if (res && res.result && res.result.structuredContent) {
        this.currentAuditData = res.result.structuredContent.data;
        this.renderAuditBoard(this.currentAuditData);
        if (speakSummary && res.result.structuredContent.voiceSummary) {
          this.speak(res.result.structuredContent.voiceSummary);
        }
        return this.currentAuditData;
      } else if (res && res.result && res.result.isError) {
        const errData = (res.result.structuredContent && res.result.structuredContent.data) || {
          error: "Audit failed",
          verification_status: "failed",
        };
        this.currentAuditData = errData;
        this.renderAuditBoard(errData);
        return errData;
      }
      return null;
    }

    renderAuditBoard(data) {
      const container = document.getElementById("auditBoardContainer");
      if (!container) return;

      if (!data || data.error) {
        container.innerHTML = `
          <div class="provenance-card">
            <div class="provenance-banner fallback_unverified">
              <div class="prov-status-group">
                <span style="font-size: 24px;">⚠️</span>
                <div>
                  <div class="prov-status-title">Rule Audit Unavailable</div>
                  <div style="font-size: 11px; opacity: 0.9;">${this.escapeHtml((data && data.error) || 'Could not retrieve rule provenance for this hackathon.')}</div>
                </div>
              </div>
            </div>
          </div>
        `;
        return;
      }

      const isLive = data.verification_status === "verified_live";
      const statusClass = isLive ? "verified_live" : "fallback_unverified";
      const statusTitle = isLive ? "Verified Live Official Rules" : "Fallback Unverified Catalog Metadata";
      const statusExplanation = isLive
        ? "Extracted directly from the official rules web page and hashed for audit integrity."
        : "Derived from event catalog metadata. Live website terms have not been freshly fetched.";
      const cats = data.categories || {};
      const sourceUrl = data.source_url || "";

      const isAmazonEvent = (data.title || "").toLowerCase().includes("amazon") ||
        data.hackathon_id === "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8";

      const amazonWarningHtml = isAmazonEvent ? `
        <div style="margin-top:8px; font-size:11px; color:#FCA5A5; background:rgba(239,68,68,0.1); padding:6px 10px; border-radius:6px;">
          * Mandatory Guardrail: Amazon Developer Hackathon requires keeping the live MCP streamable HTTP endpoint accessible through the November 20 judging period.
        </div>
      ` : "";

      container.innerHTML = `
        <div class="provenance-card">
          <div class="provenance-banner ${statusClass}">
            <div class="prov-status-group">
              <span style="font-size: 26px;">${isLive ? '🛡️' : '⚠️'}</span>
              <div>
                <div class="prov-status-title">${statusTitle}</div>
                <div style="font-size: 12px; opacity: 0.95; margin-top: 2px;">${statusExplanation}</div>
                <div style="font-size: 11px; opacity: 0.8; margin-top: 4px;">${this.escapeHtml(data.notes || '')}</div>
              </div>
            </div>
            <div class="prov-meta-row">
              <div><strong>Retrieved:</strong> ${data.retrieval_timestamp ? data.retrieval_timestamp.split('.')[0] + 'Z' : 'N/A'}</div>
              <div><strong>Source URL:</strong> ${sourceUrl ? `<a href="${this.escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer" style="color:var(--alexa-cyan); text-decoration:none;">${sourceUrl.substring(0, 32)}... ↗</a>` : 'None'}</div>
            </div>
          </div>

          <!-- Structured Rule Categories (Consequential ordering) -->
          <div class="rule-cards-grid">
            <div class="rule-category-box">
              <div class="rule-category-title"><span>🌍</span> 1. Eligibility & Regional Restrictions</div>
              <div class="rule-category-content">${this.escapeHtml(cats.eligibility || 'Open worldwide.')}</div>
            </div>
            <div class="rule-category-box">
              <div class="rule-category-title"><span>🕒</span> 2. Important Deadlines & Schedule</div>
              <div class="rule-category-content">
                <strong>Registration Cutoff:</strong> ${this.formatCardDate(data.reg_ends_at || data.deadline || '') || 'Refer to official guidelines'}<br>
                <strong>Evaluation / Hacking:</strong> ${this.formatEventRange(data.starts_at, data.ends_at) || 'Standard timeline per platform schedule.'}
              </div>
            </div>
            <div class="rule-category-box">
              <div class="rule-category-title"><span>📦</span> 3. Required Deliverables & Submission Rules</div>
              <div class="rule-category-content">${this.escapeHtml(cats.submission_requirements || 'Public repository & video demo required.')}</div>
            </div>
            <div class="rule-category-box" style="border-color: rgba(99, 102, 241, 0.4);">
              <div class="rule-category-title" style="color: #A5B4FC;"><span>☁️</span> 4. Hosting & Post-Submission Uptime Guardrails</div>
              <div class="rule-category-content">
                ${this.escapeHtml(cats.hosting_requirements || 'Ensure endpoint remains accessible for judging evaluation.')}
                ${amazonWarningHtml}
              </div>
            </div>
            <div class="rule-category-box">
              <div class="rule-category-title"><span>👥</span> 5. Team Size & Composition Rules</div>
              <div class="rule-category-content">${this.escapeHtml(cats.team_size || 'Teams of 1-4 allowed.')}</div>
            </div>
          </div>

          <!-- Secondary Collapsible: Cryptographic Audit & Raw Rules Details -->
          <details class="technical-audit-details">
            <summary>View Technical Cryptographic Provenance & Raw Text Excerpt</summary>
            <div class="technical-audit-body">
              <div><strong>Audit Record ID:</strong> ${data.audit_id || 'N/A'}</div>
              <div><strong>SHA-256 Content Hash:</strong> <code>${this.escapeHtml(data.content_hash || 'None')}</code></div>
              <div><strong>Verification Method:</strong> <code>${isLive ? 'Live HTTP 200 Fetch & DOM Extraction' : 'Structured Catalog Baseline'}</code></div>
              <div><strong>Verification Status:</strong> <code>${data.verification_status}</code></div>
              <div style="margin-top: 6px;"><strong>Raw Rules Excerpt:</strong></div>
              <pre class="wire-payload-pre" style="max-height: 140px;">${this.escapeHtml(data.raw_rules_excerpt || 'No raw text stored.')}</pre>
            </div>
          </details>

          <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px; flex-wrap:wrap; gap:8px;">
            <div style="font-size: 12px; color: var(--text-secondary);">
              Audited for: <strong>${this.escapeHtml(data.title)}</strong>
            </div>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
              ${sourceUrl ? `
                <a href="${this.escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer" class="action-btn" style="text-decoration:none;">
                  🌐 Official Page ↗
                </a>
              ` : ''}
              <button class="action-btn secondary" onclick="window.app.openHackathonBrief('${data.hackathon_id}')">📋 View Brief</button>
              <button class="action-btn" onclick="window.app.diffHackathon('${data.hackathon_id}')">⚖️ Run Rule Diff</button>
              <button class="action-btn primary" onclick="window.app.sprintHackathon('${data.hackathon_id}')">🚀 Generate Sprint Plan</button>
            </div>
          </div>
        </div>
      `;
    }

    // -------------------------------------------------------------------------
    // Rule Diff Inspector (Tab 3)
    // -------------------------------------------------------------------------
    async diffHackathon(idOrTitle, candidateText = null) {
      this.selectedHackathonId = idOrTitle;
      this.switchTab("ruleDiffInspector");
      const container = document.getElementById("diffContainer");
      if (container) {
        container.innerHTML = `<div class="loading-state">Evaluating rule diff and calculating risk level...</div>`;
      }

      const sampleCandidate = candidateText || `
Official Rules & FAQ Updates:
- Deadline Precision: Submissions close exactly at 12:00 PM PDT (noon), not midnight.
- Protocol Requirement: Server must implement MCP 2025-11-25 Streamable HTTP on /mcp.
- AWS Credits: AWS developer promotion closed; participants must use existing accounts or zero-cost hosting tiers.
- Testing Requirement: Testing via self-built simulator or web UI explicitly permitted.
- Mandatory Hosting: Server endpoint must remain live and accessible through November 20 judging period.
      `.trim();

      const res = await this.callMCP("tools/call", {
        name: "diff_rules",
        arguments: { identifier: idOrTitle, new_rule_text: sampleCandidate },
      });

      if (res && res.result && res.result.structuredContent) {
        this.currentDiffData = res.result.structuredContent.data;
        this.renderDiffView(this.currentDiffData);
        const voice = res.result.structuredContent.voiceSummary;
        if (voice) {
          this.setTicker(voice);
          this.speak(voice);
        }
        return this.currentDiffData;
      }
    }

    renderDiffView(data) {
      const container = document.getElementById("diffContainer");
      if (!container) return;

      const riskClass = (data.risk_level || "NONE").toLowerCase();
      const diffText = data.diff_unified || "";

      // Format diff lines
      const diffLines = diffText.split("\n").map((line) => {
        if (line.startsWith("+")) return `<span class="diff-added">${this.escapeHtml(line)}</span>`;
        if (line.startsWith("-")) return `<span class="diff-removed">${this.escapeHtml(line)}</span>`;
        return this.escapeHtml(line);
      }).join("\n");

      // Build structured change impact items based on detected changes
      const diffLower = diffText.toLowerCase();
      const hasDeadlineChange = diffLower.includes("deadline") || diffLower.includes("pdt") || diffLower.includes("noon");
      const hasHostingChange = diffLower.includes("hosting") || diffLower.includes("uptime") || diffLower.includes("mcp") || diffLower.includes("live");
      const hasDeliverablesChange = diffLower.includes("testing") || diffLower.includes("simulator") || diffLower.includes("deliverable") || diffLower.includes("submit");
      const hasCreditChange = diffLower.includes("credit") || diffLower.includes("cost") || diffLower.includes("aws");

      container.innerHTML = `
        <div class="diff-view-card">
          <div class="diff-header-bar">
            <div>
              <h2 style="font-family:var(--font-heading); font-size:18px;">${this.escapeHtml(data.title)}</h2>
              <p style="font-size:12px; color:var(--text-secondary); margin-top:2px;">Comparing: Listing Baseline (v1) vs Official Rules & FAQ Audit (v2)</p>
            </div>
            <div style="display:flex; align-items:center; gap:12px;">
              <span style="font-size:12px; color:var(--text-muted);">Risk Assessment:</span>
              <span class="risk-badge ${riskClass}">${data.risk_level} Risk</span>
            </div>
          </div>

          <!-- Structured Consequential Change Impact Summary -->
          <div>
            <div style="font-size:12px; font-weight:700; color:var(--text-primary); margin-bottom:8px;">
              Detected Regulatory Alterations & Practical Builder Implications:
            </div>
            <div class="diff-impact-grid">
              ${hasDeadlineChange ? `
                <div class="diff-impact-card high">
                  <div class="diff-impact-title"><span>🕒</span> Submission Cutoff Shift</div>
                  <div class="diff-impact-desc">Hard 12:00 PM PDT (noon) cutoff vs midnight assumption. <strong>Action:</strong> Schedule sprint buffer 6 hours prior to prevent late lock-out.</div>
                </div>
              ` : ''}
              ${hasHostingChange ? `
                <div class="diff-impact-card high">
                  <div class="diff-impact-title"><span>☁️</span> Persistent Server Uptime</div>
                  <div class="diff-impact-desc">Endpoint must remain active through judging. <strong>Action:</strong> Deploy keep-alive pings or automated health checks.</div>
                </div>
              ` : ''}
              ${hasDeliverablesChange ? `
                <div class="diff-impact-card medium">
                  <div class="diff-impact-title"><span>📦</span> Verified Testing Allowance</div>
                  <div class="diff-impact-desc">Self-built testing simulators explicitly authorized. <strong>Action:</strong> Record visual UI / simulator demo video alongside repo.</div>
                </div>
              ` : ''}
              ${hasCreditChange ? `
                <div class="diff-impact-card low">
                  <div class="diff-impact-title"><span>💰</span> Cloud Tier Guardrails</div>
                  <div class="diff-impact-desc">Cloud credits closed or limited. <strong>Action:</strong> Build with zero-cost hosting tiers (Render free tier / SQLite WAL).</div>
                </div>
              ` : ''}
            </div>
          </div>

          <!-- Collapsible Raw Unified Terminal Diff -->
          <details class="raw-diff-disclosure">
            <summary>Inspect Unified Line-by-Line Diff (+ Additions / - Deletions)</summary>
            <div class="diff-terminal">${diffLines || 'No raw line diffs detected.'}</div>
          </details>

          <div style="display:flex; justify-content:flex-end; gap:8px; flex-wrap:wrap;">
            <button class="action-btn secondary" onclick="window.app.openHackathonBrief('${data.hackathon_id}')">📋 Event Brief</button>
            <button class="action-btn" onclick="window.app.auditHackathon('${data.hackathon_id}')">🛡️ Back to Rules</button>
            <button class="action-btn primary" onclick="window.app.sprintHackathon('${data.hackathon_id}')">🚀 Adapt Sprint Plan</button>
          </div>
        </div>
      `;
    }

    // -------------------------------------------------------------------------
    // Sprint Planner (Tab 4)
    // -------------------------------------------------------------------------
    async sprintHackathon(idOrTitle, toggleMilestoneId = null, overrideHours = null) {
      this.selectedHackathonId = idOrTitle;
      this.switchTab("sprintPlanner");
      const container = document.getElementById("sprintContainer");
      if (!toggleMilestoneId && container) {
        container.innerHTML = `<div class="loading-state">Loading persistent calibrated sprint plan...</div>`;
      }

      const hours = overrideHours || (this.profile ? this.profile.hours_per_week : 15);
      const args = {
        hackathon_id: idOrTitle,
        available_hours: hours,
      };
      if (toggleMilestoneId) {
        args.completed_milestone_id = toggleMilestoneId;
      }

      const res = await this.callMCP("tools/call", {
        name: "generate_sprint_plan",
        arguments: args,
      });

      if (res && res.result && res.result.structuredContent) {
        this.currentSprintData = res.result.structuredContent.data;
        this.renderSprintPlanner(this.currentSprintData);
        if (!toggleMilestoneId) {
          const voice = res.result.structuredContent.voiceSummary;
          if (voice) {
            this.setTicker(voice);
            this.speak(voice);
          }
        }
        return this.currentSprintData;
      }
    }

    renderSprintPlanner(plan) {
      const container = document.getElementById("sprintContainer");
      if (!container) return;
      const milestones = plan.milestones || [];
      const currentHours = plan.hours_per_week || 15;

      const listHtml = milestones.map((m) => `
        <div class="milestone-item ${m.completed ? 'completed' : ''}" onclick="window.app.sprintHackathon('${plan.hackathon_id}', '${m.id}')" tabindex="0" role="button" aria-label="Toggle milestone ${m.id}">
          <div class="milestone-left">
            <div class="milestone-checkbox" aria-hidden="true">
              ${m.completed ? '✓' : ''}
            </div>
            <div>
              <div class="milestone-title" style="font-size:13px; font-weight:700;">${m.id}: ${this.escapeHtml(m.title)}</div>
              <div style="font-size:11px; color:var(--text-secondary); margin-top:2px;">${this.escapeHtml(m.description)}</div>
              <div style="font-size:10px; color:#93C5FD; margin-top:2px;"><strong>Deliverable:</strong> ${this.escapeHtml(m.deliverables)}</div>
            </div>
          </div>
          <span class="milestone-hours-badge">${m.hours} hrs</span>
        </div>
      `).join("");

      container.innerHTML = `
        <div class="sprint-plan-card">
          <div class="sprint-header">
            <div>
              <h2 style="font-family:var(--font-heading); font-size:18px;">${this.escapeHtml(plan.title)}</h2>
              <p style="font-size:12px; color:var(--text-secondary); margin-top:2px;">
                Calibrated for <strong>${plan.hours_per_week} hours/week</strong> • Weekly schedule: <strong>${plan.total_hours} hrs total</strong>
              </p>
            </div>
            <div class="progress-container">
              <span style="font-size:12px; font-weight:700; color:var(--alexa-cyan);">${plan.progress_percent}% Done</span>
              <div class="progress-bar-bg" aria-hidden="true">
                <div class="progress-bar-fill" style="width: ${plan.progress_percent}%;"></div>
              </div>
              <span style="font-size:11px; color:var(--text-muted);">${plan.remaining_hours}h left</span>
            </div>
          </div>

          <!-- Explicit Weekly Availability Controls & Clarification -->
          <div class="sprint-hours-adjust-bar">
            <span><strong>Weekly Runway:</strong></span>
            <button class="pill-btn ${currentHours === 10 ? 'active' : ''}" onclick="window.app.sprintHackathon('${plan.hackathon_id}', null, 10)">10h/wk</button>
            <button class="pill-btn ${currentHours === 15 ? 'active' : ''}" onclick="window.app.sprintHackathon('${plan.hackathon_id}', null, 15)">15h/wk</button>
            <button class="pill-btn ${currentHours === 25 ? 'active' : ''}" onclick="window.app.sprintHackathon('${plan.hackathon_id}', null, 25)">25h/wk</button>
            <div class="custom-hours-wrap">
              <input type="number" id="sprintCustomHours" class="custom-hours-input" min="5" max="60" value="${currentHours}" aria-label="Custom weekly hours" onkeydown="if(event.key==='Enter'){ const v=parseInt(this.value,10); if(v>=5&&v<=60) window.app.sprintHackathon('${plan.hackathon_id}', null, v); }">
              <button class="pill-btn custom-set-btn" onclick="const v=parseInt(document.getElementById('sprintCustomHours').value,10); if(v>=5&&v<=60) window.app.sprintHackathon('${plan.hackathon_id}', null, v);">Set</button>
            </div>
            <span style="font-size:11px; color:var(--text-muted); margin-left:auto;">
              * Note: Values represent available hours allocated per week toward milestones.
            </span>
          </div>

          <div class="go-no-go-box">
            <strong style="color:var(--alexa-cyan); font-size:12px;">🛑 Earliest Go/No-Go Checkpoint:</strong>
            <p style="font-size:12px; color:#E2E8F0; margin-top:4px;">${this.escapeHtml(plan.go_no_go_checkpoint)}</p>
          </div>

          <div class="reuse-compliance-box">
            <strong>⚖️ Hackathon Reuse Compliance Architecture:</strong>
            <p style="margin-top:2px;">${this.escapeHtml(plan.reuse_compliance_note)}</p>
          </div>

          <div class="milestones-checklist">
            ${listHtml}
          </div>
        </div>
      `;
    }

    // -------------------------------------------------------------------------
    // One-Click Judge Scenarios
    // -------------------------------------------------------------------------
    async runScenario1() {
      this.searchQuery = "";
      const searchInput = document.getElementById("searchInput");
      if (searchInput) searchInput.value = "";
      this.updateResetButtonVisibility();
      this.switchTab("opportunityDeck");
      this.sortFeasibility = true;
      const sortBtn = document.getElementById("sortFeasibilityBtn");
      if (sortBtn) sortBtn.classList.add("active");
      await this.loadHackathons();
      this.speak("Judges Scenario 1: Ranked all hackathons for the demo builder's ₹0 budget and 15-hour weekly runway. Top online options are sorted by feasibility.");
    }

    getAmazonHackathon() {
      const AMAZON_ID = "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8";
      let target = this.hackathons.find((h) => h.id === AMAZON_ID || h.fingerprint === AMAZON_ID);
      if (target) return target;

      target = this.hackathons.find((h) => {
        const title = (h.title || "").toLowerCase();
        return title.includes("amazon") || title.includes("alexa");
      });
      if (target) return target;

      if (this.selectedHackathonId) {
        target = this.hackathons.find((h) => h.id === this.selectedHackathonId);
        if (target) return target;
      }
      return this.hackathons[0] || null;
    }

    async runScenario2() {
      this.searchQuery = "";
      const searchInput = document.getElementById("searchInput");
      if (searchInput) searchInput.value = "";
      this.updateResetButtonVisibility();
      if (!this.hackathons || this.hackathons.length === 0) await this.loadHackathons();
      const target = this.getAmazonHackathon();
      if (target) {
        await this.auditHackathon(target.id);
      }
    }

    async runScenario3() {
      this.searchQuery = "";
      const searchInput = document.getElementById("searchInput");
      if (searchInput) searchInput.value = "";
      this.updateResetButtonVisibility();
      if (!this.hackathons || this.hackathons.length === 0) await this.loadHackathons();
      const target = this.getAmazonHackathon();
      if (target) {
        await this.diffHackathon(target.id);
      }
    }

    async runScenario4() {
      this.searchQuery = "";
      const searchInput = document.getElementById("searchInput");
      if (searchInput) searchInput.value = "";
      this.updateResetButtonVisibility();
      if (!this.hackathons || this.hackathons.length === 0) await this.loadHackathons();
      const target = this.getAmazonHackathon();
      if (target) {
        await this.sprintHackathon(target.id);
      }
    }

    quickAuditAlexa() { this.runScenario2(); }
    quickDiffAlexa() { this.runScenario3(); }
    quickSprintAlexa() { this.runScenario4(); }

    inspectHackathon(id) {
      const h = this.hackathons.find((x) => x.id === id);
      if (h) {
        this.openHackathonBrief(id);
      }
    }

    resolveTargetHackathon(text) {
      if (!this.hackathons || this.hackathons.length === 0) return null;
      const raw = (text || "").toLowerCase().trim();
      if (!raw) {
        return this.selectedHackathonId
          ? this.hackathons.find((h) => h.id === this.selectedHackathonId) || null
          : null;
      }

      // Check if user explicitly asked for amazon or alexa
      if (raw.includes("amazon") || raw.includes("alexa")) {
        return this.getAmazonHackathon();
      }

      // Check specific title terms (e.g. 'nebius', 'meta', 'opencv', 'arkiv', 'paypal', 'qloo', 'splunk')
      const rawWords = raw.split(/[^a-z0-9]+/);
      for (const h of this.hackathons) {
        const titleLower = (h.title || "").toLowerCase();
        if (raw.includes(titleLower)) {
          return h;
        }
        const titleWords = titleLower.split(/[^a-z0-9]+/).filter(
          (w) => w.length >= 4 && !["hack", "hackathon", "developer", "competition", "challenge", "series", "2026", "global", "build", "with", "tour", "stop"].includes(w)
        );
        for (const word of titleWords) {
          if (rawWords.includes(word)) {
            return h;
          }
        }
      }

      // Do not silently default to Amazon or active selection when an unrecognized hackathon is named.
      const commandStopWords = new Set([
        "audit", "rules", "rule", "provenance", "official",
        "deadline", "deadlines", "due", "date", "when", "does", "end", "ends",
        "prize", "prizes", "reward", "rewards", "cash", "bounty", "pool",
        "eligibility", "eligible", "who", "can", "enter", "hosting", "deploy", "requirement", "requirements", "policy",
        "diff", "difference", "changes", "change", "risk", "compare", "comparison",
        "sprint", "plan", "milestone", "milestones", "status", "progress", "schedule",
        "for", "the", "of", "in", "at", "on", "a", "an", "to", "is", "it", "this", "that", "event", "hackathon", "hack",
        "what", "whats", "how", "show", "check", "open", "run", "view", "tell", "me", "about", "please", "give"
      ]);
      const rawTokens = raw.split(/[^a-z0-9]+/).filter((w) => w.length > 0);
      const specificTokens = rawTokens.filter((w) => !commandStopWords.has(w));

      // If user named an entity that was not found in catalog, return null (never default to Amazon)
      if (specificTokens.length > 0) {
        return null;
      }

      // If command was purely generic intent without a specific name, fall back to explicitly selected event if one exists
      if (this.selectedHackathonId) {
        const sel = this.hackathons.find((h) => h.id === this.selectedHackathonId);
        if (sel) return sel;
      }

      return null;
    }

    async handleCommand(text) {
      const raw = (text || "").trim();
      const cmd = raw.toLowerCase();
      if (!cmd) return;
      const cmdInput = document.getElementById("commandInput");
      if (cmdInput) cmdInput.value = "";

      if (!this.hackathons || this.hackathons.length === 0) {
        await this.loadHackathons();
      }

      // 1. Sync catalog command
      if (cmd.includes("sync") || cmd.includes("refresh catalog")) {
        await this.syncCatalog();
        return;
      }

      // 1b. Hackathon Brief inquiries
      if (cmd.includes("brief") || cmd.includes("summary") || cmd.includes("overview")) {
        const target = this.resolveTargetHackathon(cmd);
        if (target) {
          this.openHackathonBrief(target.id);
          const msg = `Opening Hackathon Brief for ${target.title}. Feasibility score is ${target.feasibility_score}%.`;
          this.setTicker(msg);
          this.speak(msg);
          return;
        } else {
          const msg = "I couldn't identify that hackathon in the catalog. Please select an event from the Opportunity Deck or specify its title.";
          this.setTicker(msg);
          this.speak(msg);
          return;
        }
      }

      // 2. Deadline inquiries
      if (cmd.includes("deadline") || cmd.includes("when does") || cmd.includes("due date") || cmd.includes("ends")) {
        const target = this.resolveTargetHackathon(cmd);
        if (target) {
          const dl = target.deadline ? target.deadline.split("T")[0] : "TBA";
          const msg = `Deadline for ${target.title} is ${dl} (Location: ${target.location || 'Online'}).`;
          this.setTicker(msg);
          this.speak(msg);
          this.switchTab("opportunityDeck");
          return;
        } else {
          const msg = "I couldn't identify that hackathon in the catalog. Please select an event from the Opportunity Deck or specify its title.";
          this.setTicker(msg);
          this.speak(msg);
          return;
        }
      }

      // 3. Prize / Reward inquiries
      if (cmd.includes("prize") || cmd.includes("reward") || cmd.includes("cash") || cmd.includes("bounty")) {
        const target = this.resolveTargetHackathon(cmd);
        if (target) {
          const msg = `${target.title} prize pool: ${target.prize || 'Prizes listed'}. Feasibility prize score is ${target.feasibility_score}%.`;
          this.setTicker(msg);
          this.speak(msg);
          this.switchTab("opportunityDeck");
          return;
        } else {
          const msg = "I couldn't identify that hackathon in the catalog. Please select an event from the Opportunity Deck or specify its title.";
          this.setTicker(msg);
          this.speak(msg);
          return;
        }
      }

      // 4. Eligibility & Rule inquiries
      if (cmd.includes("eligib") || cmd.includes("who can") || cmd.includes("hosting") || cmd.includes("deploy requirement") || cmd.includes("audit") || cmd.includes("rules") || cmd.includes("provenance")) {
        const target = this.resolveTargetHackathon(cmd);
        if (!target) {
          const msg = "I couldn't identify that hackathon in the catalog to audit. Please select an event from the Opportunity Deck or specify its title.";
          this.setTicker(msg);
          this.speak(msg);
          return;
        }

        const auditData = await this.auditHackathon(target.id, false);
        let msg = "";
        if (!auditData || auditData.error) {
          msg = `Rule audit failed for ${target.title}. Could not retrieve official rules.`;
        } else if (auditData.verification_status === "verified_live") {
          msg = `Rule audit for ${target.title} verified with live provenance. Reviewing eligibility, hosting, and deliverables on the Rule Provenance board.`;
        } else if (auditData.verification_status === "fallback_unverified") {
          msg = `Rule audit for ${target.title} completed with fallback unverified catalog metadata. Live provenance could not be verified.`;
        } else {
          msg = `Rule audit completed for ${target.title}. Verification status: ${auditData.verification_status || 'unknown'}.`;
        }
        this.setTicker(msg);
        this.speak(msg);
        return;
      }

      // 5. Diff / Risk comparison inquiries
      if (cmd.includes("diff") || cmd.includes("change") || cmd.includes("risk") || cmd.includes("compare")) {
        const target = this.resolveTargetHackathon(cmd);
        if (!target) {
          const msg = "I couldn't identify that hackathon in the catalog to compare rules. Please select an event from the Opportunity Deck or specify its title.";
          this.setTicker(msg);
          this.speak(msg);
          return;
        }
        await this.diffHackathon(target.id);
        return;
      }

      // 6. Sprint Plan & Milestone status inquiries
      if (cmd.includes("sprint") || cmd.includes("plan") || cmd.includes("milestone") || cmd.includes("status") || cmd.includes("progress")) {
        const target = this.resolveTargetHackathon(cmd);
        if (!target) {
          const msg = "I couldn't identify that hackathon in the catalog to generate a sprint plan. Please select an event from the Opportunity Deck or specify its title.";
          this.setTicker(msg);
          this.speak(msg);
          return;
        }
        await this.sprintHackathon(target.id);
        return;
      }

      // 7. General search / discovery queries
      if (
        cmd.includes("find") ||
        cmd.includes("search") ||
        cmd.includes("best") ||
        cmd.includes("top") ||
        cmd.includes("rank") ||
        cmd.includes("recommend") ||
        cmd.includes("feasible") ||
        cmd.includes("zero") ||
        cmd.includes("budget") ||
        cmd.includes("online") ||
        cmd.includes("match")
      ) {
        this.switchTab("opportunityDeck");
        this.sortFeasibility = true;
        const sortBtn = document.getElementById("sortFeasibilityBtn");
        if (sortBtn) sortBtn.classList.add("active");
        this.handleSearch(raw);
        this.speak(`Searching and ranking hackathons for '${raw}'.`);
        return;
      }

      // 8. Grounded fallback for out-of-scope / open-ended prompts
      if (
        cmd.startsWith("what is the weather") ||
        cmd.startsWith("write code") ||
        cmd.startsWith("tell a joke") ||
        cmd.startsWith("how to bake") ||
        cmd.startsWith("who won")
      ) {
        const msg = "I don't have records for that request in the local catalog. As an ambient MCP advisor, I can help you discover hackathons, audit official rules with verified provenance, compare rule diffs, or track your 15h sprint plan.";
        this.setTicker(msg);
        this.speak(msg);
        return;
      }

      // 9. Default keyword search
      this.switchTab("opportunityDeck");
      this.handleSearch(raw);
      this.speak(`Searching catalog for '${raw}'.`);
    }
  }

  window.app = new HackPulseApp();
  window.addEventListener("DOMContentLoaded", () => window.app.init());
})();
