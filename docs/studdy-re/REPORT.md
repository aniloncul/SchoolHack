# Studdy AI (studdyai.com) – reverse-engineering notes

Captured 2026-10-27 with [REA](https://github.com/morluto/rea) v6.8.0 (`inspect-web-page`,
`analyze-web-bundle`) against a headless Chrome over CDP. Scope: public, unauthenticated
pages only (`/`, `/sign-in`). No backend endpoints were called or probed.

Raw evidence in this folder:

| File | Contents |
| --- | --- |
| `inspect.json` | REA page capture of `/` (DOM, accessibility text, scripts, storage keys) |
| `bundle.json` | REA static bundle analysis of `/` (routes, vendors, endpoint candidates) |
| `signin-inspect.json` / `signin-bundle.json` | Same for `/sign-in`, including script sources |
| `src/*.js` | Script sources extracted from the sign-in capture |
| `messages.en.json` | Full English i18n catalogue shipped to the client (covers the logged-in app) |

## 1. Product, in one paragraph

An **AI voice tutor that teaches on an infinite whiteboard**. The user picks a mode
(**Topic / Homework / Exam Prep**), types a prompt and/or attaches material (PDF, images,
text files, URLs), and the backend "prepares a lesson". In the tutor session a named tutor
persona (**Alfrenzo** "energetic and fun", **Rubie** "warm and friendly") speaks the lesson
while drawing it on a pannable/zoomable board. The student can interrupt by voice (mic) or
chat, see subtitles/transcript, change speech speed, reset or end the lesson, and download
the board. At the end, a summary shows **minutes, turns and concepts**. Free tier = limited
"free sessions today"; paid "Studdy Pro" weekly/monthly/yearly plans.

## 2. Tech stack (evidence-backed)

| Layer | Finding | Evidence |
| --- | --- | --- |
| Frontend | Next.js App Router + React, `next-intl` style messages (`en`, `ko`) | REA vendor fingerprints; `app/(public)/page`, `app/layout` chunks; `x-powered-by: Next.js` |
| Hosting | Vercel (`fra1` edge → `iad1` function region) | `server: Vercel`, `x-vercel-id` header |
| Auth + DB | **Supabase** project `umcvdgstciybysimigxa`, publishable key, DB schema `web_app` | `createBrowserClient(url, "sb_publishable_…", {db:{schema:"web_app"}})` in `src/93556da03ddf.js` |
| Auth methods | Google OAuth (`prompt=select_account`) and email OTP / magic link (`shouldCreateUser:true`) | `signInWithOAuth`, `signInWithOtp`, `verifyOtp` in sign-in chunk |
| Math rendering | KaTeX (jsDelivr CDN) | `cdn.jsdelivr.net/npm/katex` |
| Billing | Stripe **and** Paddle (two checkout providers) | `partnersWithLine2` / `partnersWithLine2Paddle` strings |
| Analytics | Amplitude (incl. session-replay config), Google gtag/Ads (`_gcl_ls`), OpenAI Ads pixel (`oaiq`, pixel `4nrHVCucByM2PQHx8t7vjC`) | storage keys `AMP_*`, `_gcl_ls`, `oaiq_cs:*`; `OpenAiAdsPixel` component |
| Experiments | Cookie-based A/B: `wb_landing_v3=control`, `be_experiment_name` | `set-cookie` header; module `58001` |
| Mobile | Separate iOS/Android "homework helper" app (`id6450114499`) | `/mobile-app`, store links |

## 3. Routes

Public: `/`, `/sign-in`, `/mobile-app`, `/partnerships`, `/contact`, `/privacy-policy`,
`/terms-of-service`, `/ko`.

Authenticated (from the route guard list in `src/3b690714c52e.js`):
`/home`, `/tutor-session`, `/onboarding`, `/history`, `/manage-account`, `/checkout`.
Also `/lesson/<alphanumeric id>` (accepted as a post-login `next` target, so lessons are
deep-linkable).

Server/API routes seen in the client:

| Route | Use |
| --- | --- |
| `GET /auth/callback?redirectUrl=…` | OAuth code exchange (Supabase PKCE) |
| `GET /auth/confirm?next=…` | Magic-link confirmation |
| `POST /api/auth/complete-sign-in` `{next}` → `{destination}` | Post-login routing (e.g. to `/onboarding` vs `/home`) |
| `POST /api/session/feedback` `{feedback_type, sentiment, feedback_text, session_id}` | Lesson feedback |
| `POST /api/client-log` | Batched client telemetry (≤50 events / 40 KB per batch, ≥3 s debounce) |

The lesson-creation, attachment-processing and realtime tutor transport live in the
authenticated chunks, which were **not** captured (see §6).

## 4. User flows

**Sign-in:** Google OAuth → `/auth/callback`, or email → 6-digit OTP → `verifyOtp` →
`POST /api/auth/complete-sign-in` → redirect to `destination` (default `/home`).

**Onboarding (4 steps):** name → grade (Middle/High School, College, Nursing Student,
Parent, Teacher, Professional, Other…) → spoken language → "how did you hear about us".

**Home:** greeting "What do you want to learn, {firstName}?", mode tabs, prompt box with
mode-specific placeholder, "Add File" / "Add URL", free-session counter, Start →
"Preparing your lesson…".

**Attachments:** PDF / image / text / link; SVG rejected; size and count limits; per-item
states uploading → processing → ready/error. Processing estimates: image ~15 s, PDF/text/
link ~30 s. Several photos can be merged ("Checking whether your photos are one
document…").

**Tutor session:**
- Welcome / "tap to start" gate (needed to unlock browser audio autoplay), and an
  audio-blocked recovery modal.
- Mic permission prompt ("Type or talk back to {tutorName}"). Voice input supports EN, ES,
  FR, DE, IT and PT. Other languages use the chat box, and the tutor can reply in any language.
- Board controls: pinch/scroll-zoom, swipe or Space+drag to pan, reset zoom.
- Menu: speech speed, subtitles on/off, reset lesson, end lesson, feedback, instructions.
- Transcript panel (YOU / TUTOR, with attachments), chat with attachments mid-lesson.
- Tutor picker with voice samples. The tutor can be switched mid-lesson.
- Inactivity pause plus "paused because the page was refreshed → Resume" (session state
  is server-side and resumable).
- Review mode ("Continue lesson / Exit Review"), Download Board.
- End-of-lesson modal: minutes, turns, concepts; mood-based feedback.

**Monetisation:** Free (daily session cap) → Studdy Pro weekly/monthly/yearly, plus a
"Studdy Sponsored" status (institutional partnerships, see `/partnerships`). Checkout
in-app via Stripe or Paddle.

## 5. Architecture you could replicate

```
Next.js (Vercel) ──► Supabase Auth (Google OAuth + email OTP, PKCE)
      │                   └── Postgres schema `web_app` (users, sessions, lessons, usage)
      │
      ├─ /api/* route handlers (sign-in routing, feedback, client logs, checkout)
      ├─ attachment ingest → text/vision extraction → lesson plan ("outline")
      └─ tutor session: LLM turn loop → {speech text, board draw ops}
                          ├─ TTS per persona (Alfrenzo / Rubie), speed control
                          ├─ STT for 6 languages (interruptible)
                          └─ canvas renderer with KaTeX math, pan/zoom, PNG export
```

## 6. Limits and open questions

- Only the public pages were captured. The logged-in chunks (`/home`, `/tutor-session`)
  were never loaded, so the **realtime transport** (WebSocket vs WebRTC), **LLM/TTS/STT
  vendors** and the **board draw-op format** remain unknown. The bundled Supabase Realtime
  client does not prove realtime is used.
- No network traffic was recorded: the pages had loaded before REA attached.
- The Supabase publishable key is public by design. Access is governed by RLS, which was
  not tested.

**Next step to fill these gaps:** sign in with your own account in a Chrome window started
with `--remote-debugging-port=9222`, start a lesson, then run:

```bash
rea list-browser-targets http://127.0.0.1:9222 --json
rea analyze-web-bundle http://127.0.0.1:9222 <TARGET_ID> --json > tutor-bundle.json
rea observe-web-session http://127.0.0.1:9222 <TARGET_ID> --observation-ms 30000 --json > tutor-session.json
rea inspect-web-page http://127.0.0.1:9222 <TARGET_ID> --include-websocket-shapes --include-json-body-shapes --json > tutor-inspect.json
```
