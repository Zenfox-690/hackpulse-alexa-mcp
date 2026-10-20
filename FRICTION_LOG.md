# HackPulse Alexa+ — Developer Friction Log & Product Feedback

**Amazon Developer Hackathon 2026 — Alexa+ Track**  
**Repository:** [`hackpulse-alexa-mcp`](https://github.com/Zenfox-690/hackpulse-alexa-mcp)  
**Track:** Alexa+ (Model Context Protocol & Agent Skills)  
**Date:** October 2026  
**Document Purpose:** Evidence-backed developer friction log documenting technical obstacles, debugging observations, suspected root causes, workarounds, and constructive developer experience (DX) feedback for the Amazon developer ecosystem.

---

## Executive Summary

During the development of **HackPulse Alexa+**—an autonomous hackathon discovery, rule provenance, and sprint-planning engine built on the **Model Context Protocol (MCP) `2025-11-25` Streamable HTTP** standard—our development cycle prioritized verified code behavior and genuine developer friction over unverified claims.

This report documents three development friction points, led by our most demanding debugging incident:
1. **Browser Speech Synthesis Interruption (Primary Incident):** Browser voice output cutting off mid-speech or cancelling internally during voice-first multimodal interactions.
2. **Automated Browser Verification vs. Contract Testing:** The inability to execute true headless browser end-to-end test automation, leading to a fallback to backend and static structural contract testing.
3. **Multimodal Agent Developer Experience Gaps:** The engineering overhead of developing custom simulation tooling for an ambient Echo Show 15 interface without an official Alexa+ web viewport simulator or official Python FastAPI starter templates.

---

## Incident 1 (Primary): Browser Speech Synthesis Interruption During Multimodal Voice Interactions

### 1. Specific Task
Implement reliable text-to-speech (TTS) audio narration in `static/app.js` using the Web Speech API to accompany visual card updates whenever the Alexa+ agent returns a tool response, speaks a voice summary (`structuredContent.voiceSummary`), or navigates judge demo scenarios on the Echo Show 15 interface.

### 2. Steps Taken
1. Implemented standard Web Speech API integration in `speak(text)` (Commit `5c42292`):
   ```javascript
   speak(text) {
     if (!text) return;
     document.getElementById("voiceTickerText").innerText = text;
     const utterance = new SpeechSynthesisUtterance(text);
     utterance.rate = 1.05;
     utterance.pitch = 1.0;
     window.speechSynthesis.speak(utterance);
   }
   ```
2. Tested scenario transitions (Opportunity Deck -> Rule Audit -> Rule Diff -> Sprint Planner) and natural language queries in Chromium-based desktop browsers (Google Chrome, Microsoft Edge).

### 3. Expected vs. Actual Result
- **Expected Result:**  
  The browser visibly updates the voice ticker banner and audibly narrates each voice response from start to finish across all scenario switches.
- **Actual Result:**  
  While the visual ticker text updated reliably, audible speech frequently stopped abruptly after 2 to 4 seconds, or was dropped entirely when triggering scenario actions in rapid succession.

### 4. Suspected Causes & Investigated Factors
Debugging the runtime behavior in Chromium pointed to two strong suspected causes, alongside one confirmed audio coordination factor:
- **Suspected Cause A (V8 Garbage Collection):** `SpeechSynthesisUtterance` instances created within local function scope appear to be prematurely reclaimed by the V8 garbage collector during playback when no reference is retained on an enduring object (`window` or `this`). Chromium’s internal speech dispatcher holds only a weak reference, causing the browser to emit an unhandled cancel or end event mid-sentence.
- **Suspected Cause B (Chromium Asynchronous `cancel()` Race):** Calling `window.speechSynthesis.cancel()` immediately followed synchronously by `speak(utterance)` appears to result in Chromium’s internal audio pipeline cancelling the newly queued utterance while processing the asynchronous queue reset.
- **Confirmed Factor (Microphone/TTS Collision):** Active microphone listening during audio synthesis caused the speech recognition engine to transcribe the browser's own TTS output.

### 5. Severity
- **Medium-High (Multimodal Immersion Blocker):** The visual DOM and MCP JSON-RPC backend were functional, but the voice-first experience became silent or broken during core user flows.

### 6. Workaround (Commit `958bc99`, `static/app.js` lines 160–204)
- **Root-Level Reference Retention:** Pinned the active utterance to `this._activeUtterance` and `window._activeUtterance`, releasing references only inside `utterance.onend` or `utterance.onerror`.
- **Asynchronous Cancel Debouncing:** Added a 35ms `setTimeout` delay after invoking `speechSynthesis.cancel()` before calling `speechSynthesis.speak(utterance)`:
  ```javascript
  if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
    window.speechSynthesis.cancel();
    setTimeout(() => {
      try { window.speechSynthesis.speak(utterance); }
      catch(e) { cleanupUtterance(); }
    }, 35);
  } else {
    window.speechSynthesis.speak(utterance);
  }
  ```
- **Microphone Coordination:** Enforced mutual exclusion to pause audio synthesis while microphone recording is active (`if (this.isListening) return;`), and cancelled ongoing TTS when opening the mic.
- **Automated Regression Safeguards:** Added assertions in `tests/test_mcp_e2e.py` (`test_frontend_voice_gc_protection_and_filter_reset`) ensuring `_activeUtterance`, `speechSynthesis.speaking` checks, and cancel debouncing remain in place.

### 7. Actionable Suggestion
- **Platform Distinction:** This was a client-side Web Speech API / browser runtime constraint, **not** an Amazon platform defect.
- **Suggestion for Alexa+ Developer Tools:** If Amazon provides client-side web or hybrid rendering libraries for Alexa+ visual skills, consider including a lightweight audio lifecycle utility that manages utterance retention, cancel debouncing, and microphone ducking automatically.

---

## Incident 2: Automated Browser Verification vs. Contract Testing

### 1. Specific Task
Establish automated verification for the Echo Show 15 multimodal frontend (visual card rendering, touch chips, responsive CSS tokens, and JavaScript lifecycle wiring) in CI and local test environments.

### 2. Steps Taken
1. Initially planned for automated browser verification using Playwright Chromium (noted in early documentation, commit `1dde043`).
2. Attempted to execute automated browser verification within the development environment.

### 3. Expected vs. Actual Result
- **Expected Result:**  
  An automated browser launches, navigates the web application, clicks UI elements, and verifies DOM state transitions end-to-end.
- **Actual Result:**  
  Automated browser verification could not be successfully executed in the environment. Neither Playwright nor an automated browser runner was established as a working test harness in the repository.

### 4. Root Cause Analysis
- **Tooling & Environment Barrier:** Setting up and executing automated browser testing proved impractical in the available environment, preventing true end-to-end browser execution.
- **Test Strategy Distinction:** True end-to-end browser testing (which runs an actual browser engine and simulates real user input) could not be established; the project had to pivot to structural contract assertions.

### 5. Severity
- **Medium (Test Automation Limitation):** Required relying on indirect contract verification rather than direct browser automation to validate frontend behavior in CI.

### 6. Workaround (Commits `1dde043`, `e751aae`, `958bc99`, `76d273e`)
- **Pivoted to Starlette `TestClient` Structural Contract Testing:** In place of automated browser verification, built a 16-test suite in `tests/test_mcp_e2e.py` using Starlette's `TestClient(app)`:
  - Validates MCP `2025-11-25` Streamable HTTP protocol compliance, `Mcp-Session-Id` header handling, and JSON-RPC 2.0 tool execution.
  - Fetches `/static/app.js`, `/static/styles.css`, and `/` via HTTP to assert static structural contracts: presence of GC protection variables (`_activeUtterance`), lazy speech recognition activation, microphone error states (`not-allowed`, `no-speech`), responsive `@media` breakpoints, and HTML escaping (`escapeHtml`).
- **CI Configuration:** Configured `.github/workflows/ci_and_keepalive.yml` with `python -m pytest tests/test_mcp_e2e.py -v` and `pytest.ini` (`pythonpath = .`), running test checks in seconds without browser binaries.
- **Manual Browser Cross-Check:** Performed manual interactive checks in Chrome, Edge, and Brave to confirm touch and voice behavior.

### 7. Actionable Suggestion
- **Platform Distinction:** This was a test tooling and environment obstacle, **not** an Amazon platform defect.
- **Suggestion for Hackathon Tooling:** For hackathons encouraging visual web interfaces alongside agent backends, provide recommended lightweight UI contract-testing fixtures or schemas so developers can test multimodal response formatting without needing a full browser automation harness.

---

## Incident 3: Developer Experience Gaps in Multimodal Simulation & MCP Reference Code

### 1. Specific Task
Build an autonomous MCP `2025-11-25` Streamable HTTP server in Python capable of delivering structured multimodal content (`structuredContent`) for an ambient Echo Show 15 smart display.

### 2. Steps Taken
1. Implemented a unified `POST /mcp` endpoint adhering to the MCP `2025-11-25` Streamable HTTP specification.
2. Implemented session lifecycle management via the `Mcp-Session-Id` header.
3. Searched for official Amazon developer tooling or viewport previewers to validate how custom MCP visual cards and stage transitions would render on an Alexa+ device.

### 3. Expected vs. Actual Result
- **Expected Result:**  
  A developer console previewer or web simulator (analogous to the Alexa Developer Console’s APL Simulator) where developers can preview MCP `structuredContent`, test touch navigation, and verify screen layouts for Echo Show devices.
- **Actual Result:**  
  No public web simulator was available to preview MCP visual cards on simulated Echo Show hardware. To validate the visual experience, we designed and built an Echo Show 15 web simulator from scratch (`static/index.html`, `static/styles.css`, `static/app.js`).
  
  Additionally, existing reference repositories for MCP Streamable HTTP predominantly targeted Node.js/TypeScript and `stdio` transports, leaving a documentation and starter template gap for Python FastAPI developers.

### 4. Severity
- **Low-Medium (Developer Velocity Friction):** Diverted engineering time toward constructing custom simulator tooling and implementing the streamable protocol independently.

### 5. Workaround (Commits `a37ef97`, `5c42292`, `9f3d8be`)
- **Custom Echo Show 15 Simulator:** Built an ambient dark-mode web application implementing an 80/20 canvas ratio, glassmorphic opportunity cards, provenance inspection boards, and interactive diff visualizers.
- **MCP Wire Inspector:** Added an in-app slide-out debugging drawer that exposes raw JSON-RPC 2.0 requests, responses, protocol headers, and latency metrics in real time.

### 6. Actionable Product Feedback for Amazon (`github.com/amazonappdev`)
- **Alexa+ Viewport Simulator in Developer Console:** Introduce a web-based simulator in the Alexa Developer Console allowing builders to preview MCP `structuredContent` and touch cards without physical hardware.
- **Official Python FastAPI Reference Template:** Publish an official reference repository demonstrating MCP `2025-11-25` Streamable HTTP in Python with FastAPI, including session tracking, CORS configuration, and automated test fixtures.
- **Schema Validation Decorators:** Provide SDK decorators to validate `structuredContent` stage transitions against JSON schemas, ensuring tool outputs adhere to display contracts before reaching visual canvases.

---

## Evidence Traceability Matrix

| Incident | Primary Files & Artifacts | Supporting Git Commits | Observed / Suspected Cause | Workaround & Resolution |
| :--- | :--- | :--- | :--- | :--- |
| **1. Browser Voice Interruption** *(Primary)* | `static/app.js` (L160-204)<br>`tests/test_mcp_e2e.py` (L641-651, L781-791) | `5c42292` (initial)<br>`958bc99` (fix) | Suspected V8 garbage collection of unrooted utterance + Chromium cancel/speak race. Confirmed mic audio collision. | Pinned utterance to root-level references; added 35ms cancel debounce; mic mutual exclusion; automated regression test. |
| **2. Browser Test Automation** | `requirements.txt`<br>`.github/workflows/ci_and_keepalive.yml`<br>`tests/test_mcp_e2e.py` | `1dde043` (docs note)<br>`e751aae` (pytest fix)<br>`958bc99` (contracts) | Inability to establish automated browser verification in the environment. | Pivoted to Starlette `TestClient` structural contract test suite (16 tests asserting HTTP, JSON-RPC, DOM/CSS tokens). |
| **3. Multimodal DX & Tooling Gap** | `README.md` (Section 9)<br>`app.py`<br>`core/mcp_server.py` | `a37ef97` (backend)<br>`5c42292` (simulator)<br>`9f3d8be` (feedback) | Absence of an official Alexa+ web simulator and Python FastAPI Streamable HTTP reference template. | Built custom Echo Show 15 simulator with Wire Inspector; submitted constructive developer product feedback. |

---

## Conclusion

Each incident documented in this log represents real development time spent iterating, observing runtime behavior, and implementing pragmatic workarounds during the development of HackPulse Alexa+. Addressing browser audio edge cases, implementing structural contract testing, and constructing custom simulation tooling allowed the project to deliver a functional, tested MCP prototype for the Amazon Developer Hackathon 2026.
