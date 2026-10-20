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
      this._activeUtterance = null;
      this.selectedVoice = null;
    }

    async init() {
      document.getElementById("mcpSessionBadge").innerText = this.sessionId.substring(0, 12);
      document.getElementById("drawerSessionCode").innerText = this.sessionId;

      // 1. Handshake with MCP 2025-11-25
      await this.mcpHandshake();

      // 2. Load active Builder Profile
      await this.loadProfile();

      // 3. Load catalog
      await this.loadHackathons();

      // 4. Initialize Web Speech API
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
          document.getElementById("mcpSessionBadge").innerText = this.sessionId.substring(0, 12);
          document.getElementById("drawerSessionCode").innerText = this.sessionId;
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
      document.getElementById("wireCountBadge").innerText = this.wireLogs.length;

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
      document.getElementById("wireCountBadge").innerText = "0";
      document.getElementById("wireLogsStream").innerHTML = '<div class="wire-empty-state">Wire logs cleared.</div>';
    }

    toggleWireInspector() {
      const drawer = document.getElementById("wireInspectorDrawer");
      drawer.classList.toggle("open");
    }

    // -------------------------------------------------------------------------
    // Handshake & Voice Narration
    // -------------------------------------------------------------------------
    async mcpHandshake() {
      const res = await this.callMCP("initialize", {
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

      // Coordinate lifecycles: do not play audio synthesis while user is actively recording into the microphone
      if (this.isListening) {
        return;
      }

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.05;
      utterance.pitch = 1.0;
      if (this.selectedVoice) {
        utterance.voice = this.selectedVoice;
      }

      // GC Protection: Retain on this and window to prevent V8 from prematurely terminating audio
      this._activeUtterance = utterance;
      window._activeUtterance = utterance;

      const cleanupUtterance = () => {
        if (this._activeUtterance === utterance) {
          this._activeUtterance = null;
          window._activeUtterance = null;
        }
      };
      utterance.onend = cleanupUtterance;
      utterance.onerror = cleanupUtterance;

      // Avoid Chromium/Edge synchronous cancel() race condition
      if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
        window.speechSynthesis.cancel();
        setTimeout(() => {
          try {
            window.speechSynthesis.speak(utterance);
          } catch(e) {
            cleanupUtterance();
          }
        }, 35);
      } else {
        try {
          window.speechSynthesis.speak(utterance);
        } catch(e) {
          cleanupUtterance();
        }
      }
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
      if (window.speechSynthesis) {
        const updateVoices = () => {
          const voices = window.speechSynthesis.getVoices();
          if (voices && voices.length > 0) {
            this.selectedVoice = voices.find(v => v.lang.startsWith("en") && (v.name.includes("Natural") || v.name.includes("Google") || v.name.includes("Samantha") || v.name.includes("David"))) ||
              voices.find(v => v.lang.startsWith("en")) ||
              voices[0];
          }
        };
        updateVoices();
        window.speechSynthesis.onvoiceschanged = updateVoices;
      }
      // Speech recognition is lazily initialized on user activation (toggleSpeechRecognition)
      // to avoid requesting microphone access before explicit user activation.
    }

    updateMicUI(listening, customTitle = null) {
      const mic = document.getElementById("micBtn");
      if (!mic) return;
      if (listening) {
        mic.classList.add("listening");
        mic.title = customTitle || "Listening for speech... Click to stop";
      } else {
        mic.classList.remove("listening");
        mic.title = customTitle || "Click microphone to speak";
      }
    }

    getSpeechRecognition() {
      if (this.recognition) return this.recognition;

      const SpeechClass = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechClass) {
        return null;
      }

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

        // Insert readable text into command input for user review/editing
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
            message = "Speech network error: Brave blocks Google Speech services by default. To enable voice in Brave, turn on 'Use Google services for speech recognition' in brave://settings/privacy, test in Chrome/Edge, or use the command bar below.";
          } else {
            message = "Speech recognition network connection error: The browser's external speech service is unreachable. Please use the command bar below or test in Chrome/Edge.";
          }
        } else if (err === "aborted") {
          message = "Listening stopped.";
        }
        const ticker = document.getElementById("voiceTickerText");
        if (ticker) ticker.innerText = message;

        // Auto-focus command input so user can type seamlessly without interruption
        const cmdInput = document.getElementById("commandInput");
        if (cmdInput) {
          cmdInput.focus();
        }
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
        // Stop any active TTS audio output before recording to prevent self-transcription
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
    // Builder Profile
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

      // Update hours buttons
      document.querySelectorAll(".hours-selector .pill-btn").forEach((btn) => {
        btn.classList.toggle("active", parseInt(btn.dataset.hours) === this.profile.hours_per_week);
      });

      // Update online-only toggle
      const toggle = document.getElementById("onlineOnlyToggle");
      if (toggle) {
        toggle.classList.toggle("active", Boolean(this.profile.prefer_online_only));
      }
    }

    async updateProfileHours(hours) {
      const res = await this.callMCP("tools/call", {
        name: "manage_builder_profile",
        arguments: { action: "update", hours_per_week: hours },
      });
      if (res && res.result && res.result.structuredContent) {
        this.profile = res.result.structuredContent.data;
        this.renderProfile();
        this.speak(res.result.structuredContent.voiceSummary);
        await this.loadHackathons();
      }
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
    // Catalog Discovery
    // -------------------------------------------------------------------------
    async loadHackathons() {
      const args = {
        query: this.searchQuery,
        source: this.currentFilterSource,
        sort_by_feasibility: this.sortFeasibility,
        limit: 30,
      };

      if (this.profile && this.profile.prefer_online_only) {
        args.location = "Online";
      }

      const res = await this.callMCP("tools/call", {
        name: "search_hackathons",
        arguments: args,
      });

      if (res && res.result && res.result.structuredContent) {
        this.hackathons = res.result.structuredContent.data.hackathons || [];
        document.getElementById("catalogCountPill").innerText = `${this.hackathons.length} Hackathons`;
        this.renderHackathons();
      }
    }

    renderHackathons() {
      const grid = document.getElementById("hackathonGrid");
      if (!grid) return;

      let list = this.hackathons;
      if (this.onlyBookmarks && this.profile) {
        list = list.filter((h) => this.profile.bookmarks.includes(h.id));
      }

      if (list.length === 0) {
        this.updateResetButtonVisibility(true);
        grid.innerHTML = `
          <div class="loading-state" style="text-align:center; padding: 32px 16px;">
            <div style="font-size:24px; margin-bottom:8px;">🔍</div>
            <p style="font-size:14px; color:#E2E8F0; margin-bottom:12px;">No hackathons matching current filters or query.</p>
            <button class="action-btn primary" onclick="window.app.resetFilters()" style="margin:0 auto;">Clear Search / Reset Filters</button>
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

        const reasonsHtml = (h.fit_reasons || []).slice(0, 2).map((r) => `
          <div class="fit-reason-item"><span>✓</span> <span>${r}</span></div>
        `).join("");

        const risksHtml = (h.risk_flags || []).slice(0, 1).map((r) => `
          <div class="risk-flag-item"><span>⚠️</span> <span>${r}</span></div>
        `).join("");

        return `
          <div class="hack-card" onclick="window.app.inspectHackathon('${h.id}')">
            <div>
              <div class="card-top">
                <div class="card-badges">
                  <span class="source-badge ${sourceLower}">${h.source}</span>
                  <span class="location-badge ${isOnline ? 'online' : 'in-person'}">${h.location || 'Online'}</span>
                </div>
                <div class="feasibility-ring" style="--score: ${score}" title="Feasibility Score: ${score}%">
                  <div class="feasibility-inner">${score}%</div>
                </div>
              </div>
              <h3 class="card-title" style="margin-top: 8px;">${h.title}</h3>
              <div class="card-meta-row" style="margin-top: 6px;">
                <span class="prize-pill">${h.prize || 'Prizes Listed'}</span>
                <span class="deadline-pill">Deadline: ${h.deadline ? h.deadline.split('T')[0] : 'TBA'}</span>
              </div>
              <p class="card-desc" style="margin-top: 8px;">${h.description || 'No description provided.'}</p>
            </div>

            <div>
              <div class="fit-reasons-list">
                ${reasonsHtml}
                ${risksHtml}
              </div>
              <div class="card-actions">
                <button class="action-btn" onclick="event.stopPropagation(); window.app.auditHackathon('${h.id}')" title="Audit official rules">
                  🔍 Rules
                </button>
                <button class="action-btn" onclick="event.stopPropagation(); window.app.diffHackathon('${h.id}')" title="Diff listing vs rules">
                  ⚖️ Diff
                </button>
                <button class="action-btn" onclick="event.stopPropagation(); window.app.sprintHackathon('${h.id}')" title="Open 15h sprint plan">
                  🗓️ 15h Plan
                </button>
                <button class="bookmark-icon-btn ${isBookmarked ? 'active' : ''}" onclick="window.app.toggleBookmark('${h.id}', event)" title="Toggle Bookmark">
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
      if (forceShow || this.searchQuery.length > 0 || this.currentFilterSource || this.onlyBookmarks) {
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
      document.querySelectorAll(".source-chips .chip").forEach((c) => {
        c.classList.toggle("active", c.dataset.source === "");
      });
      this.onlyBookmarks = false;
      const bmBtn = document.getElementById("filterBookmarksBtn");
      if (bmBtn) bmBtn.classList.remove("active");
      this.sortFeasibility = true;
      const sortBtn = document.getElementById("sortFeasibilityBtn");
      if (sortBtn) sortBtn.classList.add("active");
      this.updateResetButtonVisibility();
      this.loadHackathons();
      this.speak("Search query and filters reset.");
    }

    setSourceFilter(src) {
      this.searchQuery = "";
      const searchInput = document.getElementById("searchInput");
      if (searchInput) searchInput.value = "";
      this.currentFilterSource = src;
      document.querySelectorAll(".source-chips .chip").forEach((c) => {
        c.classList.toggle("active", c.dataset.source === src);
      });
      this.updateResetButtonVisibility();
      this.loadHackathons();
    }

    toggleSortFeasibility() {
      this.sortFeasibility = !this.sortFeasibility;
      document.getElementById("sortFeasibilityBtn").classList.toggle("active", this.sortFeasibility);
      this.loadHackathons();
    }

    toggleFilterBookmarks() {
      this.onlyBookmarks = !this.onlyBookmarks;
      document.getElementById("filterBookmarksBtn").classList.toggle("active", this.onlyBookmarks);
      this.renderHackathons();
    }

    async syncCatalog() {
      const btn = document.getElementById("syncCatalogBtn");
      btn.style.opacity = "0.5";
      const res = await this.callMCP("tools/call", {
        name: "sync_upstream_catalog",
        arguments: { force_refresh: true },
      });
      btn.style.opacity = "1";

      if (res && res.result && res.result.structuredContent) {
        this.speak(res.result.structuredContent.voiceSummary);
        await this.loadHackathons();
      }
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
    // Rule Provenance Audit (Tab 2)
    // -------------------------------------------------------------------------
    async auditHackathon(idOrTitle) {
      this.selectedHackathonId = idOrTitle;
      this.switchTab("ruleAuditBoard");
      const container = document.getElementById("auditBoardContainer");
      container.innerHTML = `<div class="loading-state">Executing MCP audit_rules with provenance verification...</div>`;

      const res = await this.callMCP("tools/call", {
        name: "audit_rules",
        arguments: { identifier: idOrTitle, force_live: true },
      });

      if (res && res.result && res.result.structuredContent) {
        this.currentAuditData = res.result.structuredContent.data;
        this.renderAuditBoard(this.currentAuditData);
        this.speak(res.result.structuredContent.voiceSummary);
      }
    }

    renderAuditBoard(data) {
      const container = document.getElementById("auditBoardContainer");
      const isLive = data.verification_status === "verified_live";
      const statusClass = isLive ? "verified_live" : "fallback_unverified";
      const statusTitle = isLive ? "Verified Live Official Rules" : "Fallback Unverified Catalog Metadata";
      const cats = data.categories || {};

      const isAmazonEvent = (data.title || "").toLowerCase().includes("amazon") ||
        data.hackathon_id === "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8";

      const amazonWarningHtml = isAmazonEvent ? `
        <div style="margin-top:6px; font-size:11px; color:#FCA5A5;">
          * Guardrail Warning: Amazon Developer Hackathon requires keeping the live MCP streamable endpoint online through judging (Nov 20).
        </div>
      ` : "";

      container.innerHTML = `
        <div class="provenance-card">
          <div class="provenance-banner ${statusClass}">
            <div class="prov-status-group">
              <span style="font-size: 24px;">${isLive ? '🛡️' : '⚠️'}</span>
              <div>
                <div class="prov-status-title">${statusTitle}</div>
                <div style="font-size: 11px; opacity: 0.9;">${data.notes || ''}</div>
              </div>
            </div>
            <div class="prov-meta-row">
              <div><strong>Retrieved:</strong> ${data.retrieval_timestamp ? data.retrieval_timestamp.split('.')[0] + 'Z' : 'N/A'}</div>
              <div><strong>Source URL:</strong> <a href="${data.source_url}" target="_blank" style="color:var(--alexa-cyan); text-decoration:none;">${data.source_url ? data.source_url.substring(0, 32) + '...' : 'None'} ↗</a></div>
              <div><strong>SHA-256:</strong> <code style="font-size:10px;">${(data.content_hash || '').substring(0, 10)}...</code></div>
            </div>
          </div>

          <div class="rule-cards-grid">
            <div class="rule-category-box">
              <div class="rule-category-title"><span>🌍</span> Eligibility & Geo Restrictions</div>
              <div class="rule-category-content">${cats.eligibility || 'Open worldwide.'}</div>
            </div>
            <div class="rule-category-box">
              <div class="rule-category-title"><span>👥</span> Team Size & Composition</div>
              <div class="rule-category-content">${cats.team_size || 'Teams of 1-4 allowed.'}</div>
            </div>
            <div class="rule-category-box">
              <div class="rule-category-title"><span>📦</span> Submission Deliverables</div>
              <div class="rule-category-content">${cats.submission_requirements || 'Public code repository & video demo required.'}</div>
            </div>
            <div class="rule-category-box" style="border-color: rgba(99, 102, 241, 0.4);">
              <div class="rule-category-title" style="color: #A5B4FC;"><span>☁️</span> Post-Submission Hosting & Uptime</div>
              <div class="rule-category-content">
                <strong>Hosting Policy:</strong> ${cats.hosting_requirements || 'Ensure test endpoint remains accessible.'}
                ${amazonWarningHtml}
              </div>
            </div>
          </div>

          <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px;">
            <div style="font-size: 12px; color: var(--text-secondary);">
              Audited for: <strong>${data.title}</strong>
            </div>
            <div style="display:flex; gap:8px;">
              <button class="action-btn" onclick="window.app.diffHackathon('${data.hackathon_id}')">⚖️ Run Rule Diff</button>
              <button class="action-btn primary" onclick="window.app.sprintHackathon('${data.hackathon_id}')">🚀 Generate 15h Sprint Plan</button>
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
      container.innerHTML = `<div class="loading-state">Evaluating rule diff and calculating risk level...</div>`;

      // Use Amazon hackathon sample candidate rules if none provided
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
        this.speak(res.result.structuredContent.voiceSummary);
      }
    }

    renderDiffView(data) {
      const container = document.getElementById("diffContainer");
      const riskClass = (data.risk_level || "NONE").toLowerCase();

      // Format diff lines
      const diffLines = (data.diff_unified || "").split("\n").map((line) => {
        if (line.startsWith("+")) return `<span class="diff-added">${line}</span>`;
        if (line.startsWith("-")) return `<span class="diff-removed">${line}</span>`;
        return line;
      }).join("\n");

      container.innerHTML = `
        <div class="diff-view-card">
          <div class="diff-header-bar">
            <div>
              <h2 style="font-family:var(--font-heading); font-size:18px;">${data.title}</h2>
              <p style="font-size:12px; color:var(--text-secondary); margin-top:2px;">Comparing: Listing Baseline (v1) vs Official Rules & FAQ Audit (v2)</p>
            </div>
            <div style="display:flex; align-items:center; gap:12px;">
              <span style="font-size:12px; color:var(--text-muted);">Risk Assessment:</span>
              <span class="risk-badge ${riskClass}">${data.risk_level} Risk</span>
            </div>
          </div>

          <div style="background:rgba(239, 68, 68, 0.1); border-left:4px solid #EF4444; padding:10px 14px; border-radius:6px; font-size:12px; color:#FCA5A5;">
            <strong>Detected High-Impact Regulatory Alterations:</strong>
            <ul style="margin-left: 18px; margin-top: 4px;">
              <li><strong>12:00 PM PDT Deadline:</strong> Hard noon cutoff vs midnight assumption.</li>
              <li><strong>MCP 2025-11-25 Streamable HTTP:</strong> Required on /mcp with Mcp-Session-Id.</li>
              <li><strong>Post-Submission Uptime:</strong> Backend must remain live through Nov 20 judging.</li>
            </ul>
          </div>

          <div class="diff-terminal">${diffLines || 'No raw line diffs detected.'}</div>

          <div style="display:flex; justify-content:flex-end; gap:8px;">
            <button class="action-btn" onclick="window.app.auditHackathon('${data.hackathon_id}')">🛡️ Back to Rules</button>
            <button class="action-btn primary" onclick="window.app.sprintHackathon('${data.hackathon_id}')">🚀 Adapt 15h Sprint Plan</button>
          </div>
        </div>
      `;
    }

    // -------------------------------------------------------------------------
    // Sprint Planner (Tab 4)
    // -------------------------------------------------------------------------
    async sprintHackathon(idOrTitle, toggleMilestoneId = null) {
      this.selectedHackathonId = idOrTitle;
      this.switchTab("sprintPlanner");
      const container = document.getElementById("sprintContainer");
      if (!toggleMilestoneId) {
        container.innerHTML = `<div class="loading-state">Loading persistent 15-hour sprint plan...</div>`;
      }

      const args = {
        hackathon_id: idOrTitle,
        available_hours: this.profile ? this.profile.hours_per_week : 15,
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
          this.speak(res.result.structuredContent.voiceSummary);
        }
      }
    }

    renderSprintPlanner(plan) {
      const container = document.getElementById("sprintContainer");
      const milestones = plan.milestones || [];

      const listHtml = milestones.map((m) => `
        <div class="milestone-item ${m.completed ? 'completed' : ''}" onclick="window.app.sprintHackathon('${plan.hackathon_id}', '${m.id}')">
          <div class="milestone-left">
            <div class="milestone-checkbox">
              ${m.completed ? '✓' : ''}
            </div>
            <div>
              <div class="milestone-title" style="font-size:13px; font-weight:700;">${m.id}: ${m.title}</div>
              <div style="font-size:11px; color:var(--text-secondary); margin-top:2px;">${m.description}</div>
              <div style="font-size:10px; color:#93C5FD; margin-top:2px;">Deliverable: ${m.deliverables}</div>
            </div>
          </div>
          <span class="milestone-hours-badge">${m.hours} hrs</span>
        </div>
      `).join("");

      container.innerHTML = `
        <div class="sprint-plan-card">
          <div class="sprint-header">
            <div>
              <h2 style="font-family:var(--font-heading); font-size:18px;">${plan.title}</h2>
              <p style="font-size:12px; color:var(--text-secondary); margin-top:2px;">Calibrated for ${plan.hours_per_week} hrs/week • Total Budget: ${plan.total_hours} hrs</p>
            </div>
            <div class="progress-container">
              <span style="font-size:12px; font-weight:700; color:var(--alexa-cyan);">${plan.progress_percent}% Done</span>
              <div class="progress-bar-bg">
                <div class="progress-bar-fill" style="width: ${plan.progress_percent}%;"></div>
              </div>
              <span style="font-size:11px; color:var(--text-muted);">${plan.remaining_hours}h left</span>
            </div>
          </div>

          <div class="go-no-go-box">
            <strong style="color:var(--alexa-cyan); font-size:12px;">🛑 Earliest Go/No-Go Checkpoint:</strong>
            <p style="font-size:12px; color:#E2E8F0; margin-top:4px;">${plan.go_no_go_checkpoint}</p>
          </div>

          <div class="reuse-compliance-box">
            <strong>⚖️ Hackathon Reuse Compliance Architecture:</strong>
            <p style="margin-top:2px;">${plan.reuse_compliance_note}</p>
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
      // 1. Explicit verified Amazon hackathon ID or fingerprint
      let target = this.hackathons.find((h) => h.id === AMAZON_ID || h.fingerprint === AMAZON_ID);
      if (target) return target;

      // 2. Match by title containing Amazon Developer Hackathon or Alexa
      target = this.hackathons.find((h) => {
        const title = (h.title || "").toLowerCase();
        return title.includes("amazon") || title.includes("alexa");
      });
      if (target) return target;

      // 3. Fallback to currently selected event if present, otherwise first available
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
        this.speak(`Inspecting ${h.title}. Feasibility score is ${h.feasibility_score}%. Click 'Rules' to audit or '15h Plan' to schedule.`);
      }
    }

    handleCommand(text) {
      const raw = (text || "").trim();
      const cmd = raw.toLowerCase();
      if (!cmd) return;
      const cmdInput = document.getElementById("commandInput");
      if (cmdInput) cmdInput.value = "";

      if (cmd.includes("audit") || cmd.includes("rules") || cmd.includes("provenance")) {
        this.runScenario2();
      } else if (cmd.includes("diff") || cmd.includes("change") || cmd.includes("risk")) {
        this.runScenario3();
      } else if (cmd.includes("sprint") || cmd.includes("plan") || cmd.includes("milestone")) {
        this.runScenario4();
      } else if (cmd.includes("sync")) {
        this.syncCatalog();
      } else if (
        cmd.includes("find") ||
        cmd.includes("best") ||
        cmd.includes("top") ||
        cmd.includes("rank") ||
        cmd.includes("recommend") ||
        cmd.includes("feasible") ||
        cmd.includes("zero") ||
        cmd.includes("budget") ||
        cmd.includes("match")
      ) {
        this.switchTab("opportunityDeck");
        this.sortFeasibility = true;
        const sortBtn = document.getElementById("sortFeasibilityBtn");
        if (sortBtn) sortBtn.classList.add("active");
        this.handleSearch(raw);
        this.speak(`Searching and ranking hackathons for '${raw}'.`);
      } else {
        this.switchTab("opportunityDeck");
        this.handleSearch(raw);
        this.speak(`Searching catalog for '${raw}'.`);
      }
    }
  }

  window.app = new HackPulseApp();
  window.addEventListener("DOMContentLoaded", () => window.app.init());
})();
