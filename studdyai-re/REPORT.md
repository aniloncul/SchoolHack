# Studdy AI — Tutor Session Reverse Engineering Raporu

**Hedef:** `https://studdyai.com/tutor-session/364562` ("Chemieprüfung 9. Klasse: Überblick")
**Tarih:** 2026-10-28
**Yöntem:** [morluto/rea](https://github.com/morluto/rea) üzerinden Chrome DevTools Protocol (CDP) ile pasif sayfa gözlemi + indirilen 44 JS chunk'ının statik analizi
**Kapsam:** İstemci tarafı. Sunucu kodu, prompt'lar, API gövdelerinin çalışma zamanı örnekleri ve auth altyapısı kapsam dışı (aşağıda "Bilinmeyenler").

---

## 0. Yönetici özeti

Studdy AI, **Next.js App Router + Vercel** üzerinde çalışan, sesli ve beyaz tahta üzerinden ders anlatan bir AI tutor uygulaması. Oturum ekranındaki asıl yenilik, klasik "sohbet + metin" yerine **sunucudan NDJSON komut akışı** alıp bu komutları sırayla bir beyaz tahtaya ve sese dönüştüren bir **turn motoru** olması:

- Model, `speak`, `write_latex`, `write_text`, `draw`, `erase`, `highlight`, `circle_annotate`, `finish_tutoring_lesson` gibi **komutlar** üretiyor.
- İstemci bu komutları `orderIndex` sırasına göre tamponlayıp yürütüyor, her komutu kalıcı bir **session event**'e çeviriyor.
- Olaylar `PATCH /api/session/{id}` ile delta olarak kaydediliyor; sayfa yenilenirse olay günlüğünden tahta ve ders yeniden kuruluyor.
- Ses tarafında **Deepgram (EN) / Azure Speech (diğer diller) / AssemblyAI (fallback)** ile STT, **Azure DragonHD / OpenAI TTS** ile TTS, viseme + word-boundary ile avatar dudak senkronu var.
- Model yönlendirmesi bir **"ladder"**: Gemini 3 Flash (Vertex) → Gemini 3.1 Flash-Lite → Gemini Developer API → gerektiğinde GPT/Claude/DeepSeek. Hangi rung'ın servis edildiği `x-tutor-ladder-source` yanıt başlığıyla bildiriliyor.
- Ticari taraf: Paddle ödemeleri, günlük/trial gating, `check-session-gating` ile 10 sn'de bir paywall kontrolü.

Tek cümleyle: **"LLM'in araç çağrılarını kalıcı bir olay günlüğüne yazan, olay günlüğünü de tahtaya ve sese dönüştüren bir realtime ders motoru."**

---

## 1. Kanıt tabanı ve sınırlar

### Kullanılan REA komutları

```bash
rea list-browser-targets http://127.0.0.1:9222 --allowed-origins https://studdyai.com
rea inspect-web-page       http://127.0.0.1:9222 <targetId> --include-script-sources ...
rea analyze-web-bundle     http://127.0.0.1:9222 <targetId> --allowed-origins https://studdyai.com
rea export-web-scripts     page-with-sources.json scripts/
rea analyze-javascript-application scripts/ --source-path <tutor-session chunk>
rea capture-web-screenshot http://127.0.0.1:9222 <targetId>
```

Üretilen kanıt dosyaları `rea-runs/studdyai/` altında; okunabilirlik için küçültülmüş (beautify) kaynaklar `rea-runs/studdyai/pretty/` altında.

### Kritik sınırlar

| Sınır | Etki |
|---|---|
| Ağ trafiği sayfa yüklendikten **sonra** dinlendi | Canlı istek/yanıt gövdeleri yakalanmadı; şemalar kod okumasından çıkarıldı |
| CDP passif | Hiçbir JS enjekte edilmedi, kullanıcı oturumu sürülmedi, API'ye istek atılmadı |
| Kaynak haritaları yok | Orijinal değişken adları yok; minified kod okundu |
| 44 chunk'ın tamamı anlamsal analize sokulmadı | 300 sn/source limiti nedeniyle yalnızca hedef chunk + seçili chunk'lar semantik analiz edildi; string/davranış taraması tüm dosyalarda yapıldı |
| Sunucu kodu yok | Sistem prompt'u, RAG dokümanları, model ladder kararı, auth doğrulaması **gözlemlenmedi** |

> Not: Rapor, kullanıcının kendi açtığı oturumun istemci kodunu inceler. Auth atlatma, ödeme kaçırma veya başka hesaplara erişim denenmedi.

---

## 2. Uygulama mimarisi

### Stack (gözlemlenen)

| Katman | Teknoloji | Kanıt |
|---|---|---|
| Framework | Next.js App Router (`/_next/static/chunks/app/(app)/...`) | chunk yolları |
| Hosting | Vercel (`dpl_AzRgfhPtbqpxN7qHiztsUnL2WLc5`, `VERCEL_ENV`) | chunk query string |
| UI | React 19, Tailwind utility sınıfları, KaTeX, react-markdown, lucide benzeri ikonlar | chunk içerikleri |
| Feature flags | LaunchDarkly (`NEXT_PUBLIC_LAUNCH_DARKLY_CLIENT_SIDE_ID`, `branch_experiments`) | s18 |
| Analytics | Amplitude (+ Vercel Analytics) | s17/s18 |
| Ödeme | Paddle Billing (`cdn.paddle.com/paddle/v2/paddle.js`) | s18/s27 |
| Ses | Azure Speech (TTS + STT), Deepgram, AssemblyAI | s21/s36/s40 |
| Hata/olay logu | `/api/client-log` | s17/s18/s24 |
| Auth | İstemcide Supabase/Firebase/Clerk SDK **yok**; sunucu taraflı cookie/oturum | negatif gözlem |

`__session` isimli bir cookie bekleniyor olabilir ancak istemci kodunda doğrudan token yönetimi yok; bu, auth'un tamamen sunucu/middleware katmanında olduğunu gösteriyor (**çıkarım**).

### Sayfa yapısı

`/tutor-session/[id]` rotası iki modu destekliyor:

- Normal: `loadSession(id, userId)`
- `?preview=true`: `previewSession(...)` — ses bağlanmadan, sadece tahta/transcript önizlemesi
- Geçmiş/bitmiş ders: `isReviewMode` — gating veya `finishReason` (`paywalled|completed|user_ended|reset`) nedeniyle salt-okunur mod

Ekran (ekran görüntüsü `rea-runs/studdyai/session.png`):

- Üst sol: menü / navigation tray ("Boards")
- Üst sağ: "Download Board", "End Lesson", model rozeti (yalnızca yetkili hesaplarda)
- Orta: el yazısı görünümlü beyaz tahta (SVG/HTML elementleri), animasyonlu silme
- Alt sol: avatar + mikrofon durumu + transcript butonu + oynat/duraklat
- Alt orta: canlı altyazı (AI konuşması)
- Alt sağ: metin girişi, mikrofon, caption
- Modallar: welcome, volume, mic permission, inactivity ("Lesson session paused"), paywall, refresh-resume, audio blocked

---

## 3. Oturum yaşam döngüsü

### 3.1 Oluşturma (home)

`POST /api/session/create`

```jsonc
{
  "sessionType": "topic" | "homework" | "exam",
  "userTextInput": "Chemieprüfung 9. Klasse ...",
  "attachments": [ /* fileUploads */ ],
  "lifetimeValidSessionsCount": 12,
  "studentNameOverride": null,
  "metadata": {
    "appVersion": "1.0.7",
    "boardDimensions": { "width": 2316, "height": 1178 }
  },
  "academyLessonToken": null,
  "denseCoverage": { "arm": "dense" | "control", "consolidation": { ... } }
}
```

Yanıt: `{ "sessionId": 364562 }` → `router.push('/tutor-session/364562')`.

### 3.2 Yükleme

Paralel iki istek:

1. `GET /api/session/{id}` → `{ sessionHead, finishReason?, title?, timeSpent?, ... }`
2. `GET /api/session/{id}/events?from=0&limit=1000` → `{ events: [...], total }`

`total > 1000` ise kalan sayfalar 1000'lik parçalarla çekilir. `sessionHead` + `events` birleşimi **Session Data**'yı oluşturur.

Gating paralel çalışır: `POST /api/user/check-session-gating`.

`isReviewMode` kararı:

```
shouldStop || (!isPaid && finishReason in [paywalled, completed, user_ended, reset])
```

### 3.3 Başlatma

- `session_started` (veya resume ise `session_resumed`) event'i eklenir.
- `POST /api/session/create-title {sessionId}` ile başlık asenkron üretilir.
- Ses bağlanır (TTS + uygun STT).
- İlk tur otomatik tetiklenir: `POST /api/session/generate-response`.
- İlk derste onboarding modalları (welcome → volume → mic permission → coachmark) gösterilir.

Resume mantığı: son turn kullanıcıya aitse `session_resumed` metni "son mesaj hâlâ bekliyor, doğrudan yanıtla" şeklinde system talimatı içerir; reset sonrası dönüşte farklı metin kullanılır.

### 3.4 Tur döngüsü

```
kullanıcı konuşur (veya yazar)
  → message_sent_by_user event'i (turnIndex = lastTurnIndex + 1)
  → PATCH /api/session/{id} (delta persist)
  → POST /api/session/generate-response
  → NDJSON komut akışı
  → komutlar sırayla yürütülür
  → her komut session event'ine dönüşür
  → event'ler tahtaya/sese/altyazıya yansır
  → delta persist
  → tur bitince context collapse kontrolü
```

Kullanıcı AI konuşurken araya girerse (`userStartedSpeaking`):
- istek abort edilir,
- yarım kalan (persist edilmemiş) AI event'leri silinir (`removeEventsMatching`),
- tahta yeniden kurulur.

### 3.5 Bitirme / sıfırlama

| İşlem | Endpoint | Not |
|---|---|---|
| Ders bitir | `POST /api/session/{id}/finish {reason,timeSpent}` | `abandoned` için `navigator.sendBeacon` |
| Bitirme nedenini temizle | `POST /api/session/{id}/clear-finish-reason` | resume'da |
| Sıfırla (yeni tahta) | `POST /api/session/reset {originalSessionId}` → `{newSessionId}` | `session_ended_with_reset` event'i |
| Kalp atışı | `POST /api/session/{id}/heartbeat {timeSpent}` | 10 sn'de bir |
| Başlık | `POST /api/session/create-title`, `PATCH /api/session/update-title` | |
| PDF | `POST /api/session/{id}/pdf {html}` → blob | "Download Board" |

`reason` değerleri: `user_ended`, `completed`, `paywalled`, `reset`, `abandoned`.

### 3.6 Delta senkronizasyonu (önemli tasarım detayı)

İstemci, sunucudaki son hâli `persistedEvents` + `persistedHeadJson` + `persistedCount` olarak tutar. Her yazmada:

```jsonc
PATCH /api/session/{id}
{
  "appended": [ /* yeni event'ler */ ],
  "removed":  [ /* silinen event pozisyonları */ ],
  "head":     { /* sadece head değiştiyse */ },
  "persistedCount": 123
}
```

- Pozisyon anahtarı: `turnIndex.orderIndex.subOrderIndex` (+ varsa `#elementId`).
- Yanıt: `{ persistedCount }`.
- **409 Conflict** → başka sekme/eski kopya: istemci sayfayı yeniler (`window.location.reload()`).
- Yazma kuyruğu tek eşzamanlılıkta çalışır.

Bu, CRDT'ye benzemeyen, "tek yazar + iyimser kilit" yaklaşımıdır.

---

## 4. Turn protokolü — ürünün kalbi

### 4.1 İstek

`POST /api/session/generate-response`

```jsonc
{
  "sessionId": 364562,
  "delta": {
    "appended": [ { "name": "message_sent_by_user", "text": "...", "turnIndex": 7, "orderIndex": 0, "subOrderIndex": 0, "type": "chat" } ],
    "removed": [],
    "head": { /* değiştiyse */ }
  },
  "tutorModel": "gemini-3-flash-preview"   // yalnızca model seçimi yetkisi olanlarda
}
```

Yanıt: `Content-Type: text/event-stream`, gövde **düz NDJSON** (`data:` öneki yok) — **canlı doğrulandı**, 28.10.2026, 7 çağrı / 88 çerçeve. İstemci `fetch` + `ReadableStream` ile okuyor, `EventSource` değil. Satırlar `\n` ile ayrılıyor; bozuk satırda tur iptal.

> `x-tutor-ladder-source` başlığı 7 yanıtın **hiçbirinde gözlenmedi**; kodda sabit olarak var, sunucu koşullu gönderiyor olmalı. Doğrulanamadı.
> Gerçek çerçeveler: [`live-protocol.md`](live-protocol.md)

### 4.2 Akış çerçeveleri

İki tür satır var:

1. **Komut çerçevesi** — `commandName` + `orderIndex` + komuta özel alanlar
2. **Veri çerçevesi** — `{ "_stream": "svg", "elementId": "...", "chunk": "<svg parçası>" }` → ilerlemeli çizim (progressive draw). `_stream` varsa komut yürütülmez, doğrudan tahtaya eklenir.

### 4.3 Komut tablosu

| `commandName` | Ana alanlar | Ürettiği event |
|---|---|---|
| `speak` | `text`, `style` (`chat`\|`friendly`), `styledegree` | `message_received_from_tutor` (canlı doğrulandı) |
| `write_latex` | `elementId`, `position{spaceId,columnId}`, `latex`, `color`, `scaleFactor` | `whiteboard_element_added/edited` (`contentType:"latex"`) |
| `write_text` | `elementId`, `position`, `text` (HTML) | `whiteboard_element_added/edited` (`contentType:"html"`) |
| `draw` | `elementId`, `position`, `prompt`, `isStructured`, `maxDimensions` → yanıtta `maxDims`, `fileType:"png"`, `generationState` | `whiteboard_element_added` (boş) → `whiteboard_element_edited` (görsel URL'i) |
| `erase` | `eraseAll:true` veya `ids:[]` | `whiteboard_cleared` / `whiteboard_element_removed` |
| `highlight` | `annotationId`, `targetElementId`, `color`, `textToMatch`, `textToMatchIndex` | `whiteboard_annotation_added` (`highlight`) |
| `circle_annotate` | aynı | `whiteboard_annotation_added` (`circle`) |
| `finish_tutoring_lesson` | `number_of_concepts_learned` | `session_ended_by_tutor` |
| `document_retrieved` | `docSlug`, `docTitle`, `content`, `thoughtSignature` | `document_retrieved` |
| `chunk_retrieved` | `docSlug`, `docTitle`, `indexPath`, `chunkTitle`, `content`, `physicalAddress`, `thoughtSignature` | `chunk_retrieved` |
| `collapsed_block_expanded` | `collapseId`, `fromTurn`, `toTurn`, `turnContents`, `thoughtSignature` | `collapsed_block_expanded` |
| `skill_loaded` | `skillSlug`, `skillName`, `version`, `encryptedContent`, `thoughtSignature` | `skill_loaded` |
| `lesson_plan_updated` | `content`, `instruction` | `lesson_plan_updated` |
| `command_failed` | `actionItem` | `action_failed` |

Her komut isteğe bağlı `actionItem` taşıyabilir; istemci yürütme sonrası `actionItem.response = {success, id?}` olarak geri yazar. Bu, modelin "ben bu aracı çağırdım" varsayımını doğrulamasını sağlar.

`write_latex` / `write_text` boş içerikle gelirse **atılır** (kod yorumu: "AI misbehavior — spaces/columns are created implicitly"). Geçersiz LaTeX gelirse `/api/session/fix-latex` ile onarılmaya çalışılır; olmazsa komut yine de uygulanır.

### 4.4 Yürütme motoru

```
ingest (async) ──► Map<orderIndex, handler>
                      │
executor ◄────────────┘
  - orderIndex 0'dan başlar
  - sıradaki yoksa bekler (15 sn uyarı)
  - handler.prepare() → handler.execute(sessionSnapshot)
  - event'ler session'a eklenir, UI'a yield edilir
  - akış bitti + map boş → tur biter
```

- Handler'lar `orderIndex`'e göre sıralı çalışır; ağdan gelen sıra farklı olabilir.
- 15 sn'de komut gelmezse "Turn stalled", sıra boşluğu varsa "Turn wedged" logu.
- Komut hatası tüm turu düşürmez; o komut atlanır.
- Tur sonunda event sayısı 0 ise "no commands executed" uyarısı.

### 4.5 Session event zarfı

```jsonc
{
  "timestamp": "2026-10-28T12:34:56.789Z",
  "turnIndex": 7,
  "orderIndex": 2,
  "subOrderIndex": 0,
  "name": "whiteboard_element_added",
  "elementId": "el_...",
  "position": { "spaceId": "s1", "columnId": "c2" },
  "contentType": "latex",
  "content": "\\frac{1}{2}",
  "style": { "color": "#111", "scaleFactor": 1.4 }
}
```

Sıralama: `turnIndex → orderIndex → subOrderIndex`.

Event kataloğu (istemci tarafı):

| Grup | Event'ler |
|---|---|
| Sistem | `session_started`, `session_resumed`, `session_reset`, `session_ended_by_user`, `session_ended_by_tutor`, `session_ended_with_paywall`, `session_ended_with_reset` |
| Mesaj | `message_sent_by_user`, `message_received_from_tutor` |
| Tahta | `whiteboard_element_added`, `whiteboard_element_edited`, `whiteboard_element_removed`, `whiteboard_cleared`, `whiteboard_annotation_added` |
| Bağlam | `context_collapsed`, `collapsed_block_expanded` |
| Materyal | `document_retrieved`, `chunk_retrieved`, `skill_loaded`, `lesson_plan_updated` |
| Ayar | `speech_speed_changed`, `user_speech_language_changed`, `captions_enabled`, `captions_disabled` |
| Hata | `action_failed` |

### 4.6 Beyaz tahta modeli

- **Space** → **Column** → **Element** hiyerarşisi.
- İçerik tipleri: `latex`, `html`, `svg-drawing-inline`, `svg-drawing-file`, `image-file`.
- Aynı `elementId` ikinci kez gelirse `added` yerine `edited` üretilir (silinmişse yeniden `added`).
- Silme animasyonu geciktirilir: `whiteboard_element_removed` önce "erasing" kuyruğuna alınır; aynı element tekrar gelirse silme iptal edilir.
- `svg` akışı: `_stream` çerçeveleri parça parça birleştirilir; tam içerik gelince streaming buffer temizlenir.

**Yerleşim — anlam modelde, geometri istemcide:**

- Model yalnızca `{spaceId, columnId}` seçer; x/y hiçbir event'te yok (40 dk / 37 element tarandı — `position` anahtarları sadece `spaceId`, `columnId`). Piksel koordinatı istemcide deterministik hesaplanır.
- Layout sabitleri (`s29.js`): `defaultColumnWidth:480`, `elementGap:24`, `columnGap:40`, `spaceGap:80`, `rowGap:160`, `maxSpacesPerRow:3`. Sütun içi dikey yığın (ölçülen yükseklik + gap), sütunlar soldan sağa, 3 space sonra satır atlanır.
- Sıra = elementin **ilk göründüğü an** (`turnIndex→orderIndex→subOrderIndex`); düzenleme sırayı bozmaz, yalnızca space/column değişimi yeniden damgalanır.
- Kamera en son değişen elementi izler (öncelik: yeni > düzenlenen > anotasyon > boyut); ilk space/sütun dışındaysa `onNewColumnPan()`. Kullanıcı uzaklaşırsa "Follow me" (`refocusAvailable`) belirir.
- Ölçülen büyüme: 21 turluk Almanca dersinde 1 space / 7 sütun ≈ 3.640 px (viewport ~1440); 40 dk boyunca `erase`/`removed`/`cleared` = 0. Tahta dağılmıyor, **tek yönde ve temizliksiz** büyüyor. Sütun sırası kronolojiktir, niyet sırası değil.
- `getLastUsedPosition()` (`s8.js:3670`, `s36.js:1609`) tanımlı ama hiç çağrılmıyor — "en son neredeydim" bilgisi modele verilmiyor.

---

## 5. Ses hattı

### 5.1 STT seçimi

```js
getSTTTypeForLanguage(lang) {
  return lang === "en" ? "deepgram" : "azure";
}
// fallback: AssemblyAI
```

| Sağlayıcı | Protokol | Model / detay |
|---|---|---|
| **Deepgram** | WebSocket | `flux-general-en`, `linear16`, 16 kHz |
| **Azure Speech** | Speech SDK 1.47.0 + `/api/voice/get-token` | `tokenId:"token-3"`, region `eastus`, 16 kHz/16-bit/mono PushStream |
| **AssemblyAI** | `wss://streaming.assemblyai.com/v3/ws` | `universal-streaming-english` / `universal-streaming-multilingual`, `pcm_s16le`, 16 kHz, `format_turns=true` |

Ortak akış:
1. Mikrofon → AudioContext (16 kHz) → `pcm-converter-processor` AudioWorklet
2. `/api/voice/get-token {tokenId:"token-1"}` → `{ token }` (ephemeral)
3. WebSocket bağlantısı; 100 ms'lik PCM chunk'ları
4. Partial transcript → `userStartedSpeaking`
5. Final transcript → `userFinishedSpeaking(text)` → `message_sent_by_user`
6. 6 sn (T.N4) sessizlik → turn timeout ile transcript'i yine de gönder
7. Kopmada üstel/lineer backoff, max 10 deneme

Oturum sonunda `POST /api/voice/track-stt-session` ile sağlayıcı, model, ses/session süresi raporlanır.

> **Canlı doğrulandı (28.10.2026, Almanca oturum — Azure yolu):** `tokenId` gerçekte **`"token-3"`** (kodda `token-1` varsayılanı var). JWT `region: eastus`, `product: SpeechServices.S0`. SDK doğrudan Azure protokolünü konuşuyor: `speech.config` → `recognition:"conversation"`, `speech.context` → `phraseDetection{Conversation, segmentationSilenceTimeoutMs:1000}`, ardından `speech.hypothesis` / `speech.phrase` olayları. `PrimaryLanguage` sunucudan `de-DE` geliyor — dil istemciden zorlanmıyor. Kapanış: `track-stt-session{provider:"azure", model:"azure-stt", closeReason:"muted", audioDurationSeconds, sessionDurationSeconds}`. Ayrıntı: [`live-protocol.md §10`](live-protocol.md).

### 5.2 TTS

İki implementasyon: **Azure** (varsayılan) ve **OpenAI**. İkisi de `/api/voice/get-tts` endpoint'ini bir **Web Worker** içinden çağırır.

İstek config'i (worker'a postMessage):

```jsonc
{
  "apiPath": "https://studdyai.com/api/voice/get-tts",
  "voice": "en-US-Andrew3:DragonHDLatestNeural",
  "locale": "en-US",
  "sampleRate": 24000,
  "speed": 1,
  "model": "model-3",
  "prebufferMs": 640,
  "style": "cheerful",
  "styleDegree": 1.2,
  "includeTimestamps": true,
  "sessionId": 364562,
  "userId": "...",
  "clientId": "...",
  "turnIndex": 7,
  "actionInternalId": "..."
}
```

- Yanıt: PCM/audio chunk'ları + `viseme` + `wordBoundary` olayları.
- İstemci `AudioContext` ile chunk'ları **çalan zamana göre** sıraya koyar (prebuffer 640 ms), böylece akış kesintisiz.
- Viseme kuyruğu `requestAnimationFrame` ile avatar ağzına işlenir; word boundary altyazı vurgusu için.
- `aiSpeechStyleChanged` → avatar ifadesi (`angry→angry`, `cheerful→smile`, `sad→frown`, ...).
- LaTeX konuşmaya çevrilir: KaTeX → MathML → `speech-rule-engine` (clearspeak) → İngilizce matematik telaffuzu.
- Markdown (`**`, `_`, HTML) sesten önce temizlenir.
- Hız 0.25–4× arası; caption hız faktörü varsayılan 1.29.

> **Doğrulandı (28.10.2026):** TTS worker modülü bulundu ve çözüldü — `_next/static/chunks/7488.016d207a7f1f7f57.js`, 3,4 KB. Tel formatı: **5 baytlık başlık** (`type:u8` + `length:u32 LE`) + gövde; `type 0` = PCM Int16 LE, `type 1` = viseme JSON, `type 2` = word-boundary JSON. `prebufferMs` sunucuya gitmiyor, istemcide preroll baytı hesaplıyor (`2 × 24000 × 0.64 = 30720`). Tam protokol: [`live-protocol.md §9`](live-protocol.md).

### 5.3 Ses / dil matrisi

TTS sesleri (DragonHD / DragonHDOmni):

| Dil | Ses |
|---|---|
| en | `en-US-Andrew3:DragonHDLatestNeural` |
| de | `de-DE-Florian:DragonHDLatestNeural` |
| es | `es-ES-Tristan:DragonHDLatestNeural` |
| fr | `fr-FR-Remy:DragonHDLatestNeural` |
| ja | `ja-JP-Masaru:DragonHDLatestNeural` |
| zh | `zh-CN-Yunfan:DragonHDLatestNeural` |
| ar | `ar-SA-Hamed:DragonHDOmniLatestNeural` |
| ... | bg, bn, el, hi, id, it, ko, pl, ru, sv, sw, th, tr, uk, ur, pt |

Tutor bazlı override: **Rubie** → `en-US-Nova:DragonHDLatestNeural` (fallback `en-US-Ava`), **Alfrenzo** → dil varsayılanı.

---

## 6. Model yönlendirme ("ladder")

İstemci, sunucunun döndürdüğü `x-tutor-ladder-source` başlığını izler. Kaynak etiketleri: `admin`, `granted`, `demo`, `opus_start`, `ops`; yoksa **production**.

İstemcide tanımlı model kataloğu (yetkili kullanıcıların seçebildiği):

| ID | Sağlayıcı / platform | Not (koddan) |
|---|---|---|
| `gemini-3-flash-preview` | Gemini / Vertex | **Production ilk rung** |
| `gemini-3.1-flash-lite` | Gemini / Vertex | Production fallback (2. ve 3. rung) |
| `gemini-3-flash-preview@api` | Gemini / Developer API | Vertex kesintisi bypass'ı |
| `gemini-3.8-flash` | Gemini / Vertex | En yeni Flash; MINIMAL thinking yok |
| `gemini-3.5-flash-lite` | Gemini / Vertex | En yeni Flash-Lite |
| `gpt-6.1-sol` | OpenAI | reasoning kapatılamaz, low |
| `gpt-6.1-sol-fast` | OpenAI fast tier | 2× fiyat, ~4× hızlı ilk çıktı |
| `gpt-6-astra` | OpenAI | |
| `gpt-6-astra-ultrafast` | OpenAI ultrafast | 6× fiyat, ~5× decode (310 vs 58 tok/s) |
| `gpt-6-luna` | OpenAI nano | |
| `claude-opus-5-5` | Anthropic / Bedrock | thinking zorunlu, effort low |
| `claude-sonnet-5-5` | Anthropic / Bedrock | |
| `claude-fable-5` | Anthropic | thinking zorunlu |
| `claude-fable-5-1` | Anthropic / Bedrock | **demo dersleri**, TTFT ~2.5 s |
| `deepseek-v4-flash` | OpenRouter | **öğrenciler için elenmiş**; tahta araçlarını bazen boşa çağırıyor, p90 TTFT 104 s |

`GET /api/user/tutor-models` kullanıcıya izinli modelleri döndürür; boş liste model seçiciyi gizler. Oturum açmamış kullanıcıda tüm liste görünür (geliştirme/demo kolaylığı).

Model seçimi yalnızca **sonraki turdan** itibaren geçerli; UI metni: "From the next turn, on your lessons".

---

## 7. Bağlam yönetimi

- Her tur sonunda `collapseContextUseCase.checkAndCollapse` çalışır.
- İstemci, son **24 model turn**'ünü korur; daha eski "middle" turn'ler için sunucuya `POST /api/session/collapse-context` gider.
- Sunucu **token eşiğine göre** `{segments:[{summaryText, fromTurn, toTurn}]}` döner; boşsa işlem yapılmaz.
- İstemci her segment için `context_collapsed` event'i yazar (`collapseId`, `summaryText`, `collapsedFromTurn`, `collapsedToTurn`).
- Model daha sonra `collapsed_block_expanded` komutuyla bir bloğu geri açabilir (`fromTurn`, `toTurn`, `turnContents`).
- Sadece fonksiyon çağrısı içeren turn'ler (`document_retrieved`, `chunk_retrieved`, `skill_loaded`, `collapsed_block_expanded`) "model turn" sayılmaz.

Bu, uzun derslerde maliyeti düşürmenin ana mekanizması.

---

## 8. Gating / paywall / trial

İki seviye:

### 8.1 Oturum başlatma (home)

`POST /api/user/check-session-gating`

Yanıt alanları: `isPaid`, `isAtDailySessionLimit`, `isTrialExhausted`, `hardPaywallEnabled`, `lifetimeValidSessionsCount`.

```
shouldStop = !isPaid && (isAtDailySessionLimit || (isTrialExhausted && hardPaywallEnabled))
```

### 8.2 Ders sırasında

- 10 sn'de bir `POST /api/user/check-session-gating {sessionId}`.
- Yanıt `{status:"success", stopReason, ...metrics}`.
- `stopReason !== null` → ders duraklatılır, paywall modal açılır, `isPaymentGated=true`, gating interval durur.
- Kapatınca `enterLessonReviewMode()` (salt-okunur) veya "End Lesson".
- `session_ended_with_paywall` event'i + `finish(reason="paywalled")`.

### 8.3 Ödeme

- Paddle Billing v2 (classic de destekli).
- `GET /api/pricing/paddle-localized` → yerelleştirilmiş fiyat.
- `POST /api/user/create-paddle-transaction`, `create-phone-trial-transaction`, `mobile-trial-started`, `restore-subscription`.
- Planlar: `annual` (year×1), `quarterly` (month×3); phone trial express frame.
- `?upgrade=success` / `paddle_success` query'si ile başarı akışı.

### 8.4 Trial sayaçları

- `studdy_session_count` localStorage anahtarı.
- `lifetimeValidSessionsCount` sunucudan gelir ve create isteğine geri gönderilir (server-side doğrulama için).
- `sessionStorage: tutor_session_active_<id>` ile aynı oturumun çift sekmede açılması izlenir.

---

## 9. Dosya / link / RAG hattı

### 9.1 Yükleme

1. `POST /api/session/signed-upload-urls {files:[{fileName,mimeType}], sessionId?}`
   → `{uploads:[{storagePath, signedUrl, thumbnailStoragePath, thumbnailSignedUrl}]}`
2. `PUT signedUrl` (doğrudan storage'a; S3/R2/GCS uyumlu imzalı URL)
3. `POST /api/attachment/process` veya `process-with-status-streaming`
   → doküman nesnesi: `{ chunks, processing:{path:"full_text"|"chunked", page_count}, extractedText, ... }`
4. Thumbnail varsa ayrı imzalı URL'e `PUT`

### 9.2 Doküman işleme

- PDF: sayfa sayısı, thumbnail, metin/parça çıkarımı
- Görsel: doğrudan modele görsel olarak
- Link: `process-link-streaming` (SSE benzeri NDJSON durum akışı) + favicon/thumbnail
- Metin: düz metin
- `consolidate-images`: çoklu görseli tek bağlama indirger
- `update-prompt-suggestions`: çıkarılan metinden `{topic, exam, homework}` önerileri üretir

### 9.3 RAG / bilgi tabanı

Olaylardan görülen sunucu tarafı yapı:

- `document_retrieved`: `docSlug`, `docTitle`, `content`, `thoughtSignature`
- `chunk_retrieved`: `indexPath`, `chunkTitle`, `physicalAddress`, `content`
- `skill_loaded`: `skillSlug`, `skillName`, `version`, `encryptedContent`

Bu, modelin bir **ders dokümanı deposundan** (`/api/doc-repo/...`) parça çektiğini, sürümlü **skill** paketlerini şifreli olarak bağlama yüklediğini gösteriyor. `thoughtSignature` alanları, Gemini "thought signature" mekanizmasının oturumlar arası taşındığını düşündürüyor.

---

## 10. Tutor kişilikleri

İstemcide tanımlı iki tutor var (sunucu tarafında daha fazlası olabilir):

### Alfrenzo (varsayılan)
```
Profile: 34yo (DOB April 1st), UMich Biophysics grad, you work as a teacher for Studdy.
Family: Wife (Lily), Kids (Liam 7, Oliver 4).
Interests: Soccer, Baseball, Movie "Titanic".
```
Ses: dil varsayılanı. Çizimler: `alfrenzo-lesson`, `alfrenzo-card`.

### Rubie
```
Profile: 40 year old tutor. she is warm and friendly, encouraging but also to the point,
cool, smart and sometimes tough. She is knowledgeable, appreciative, and result driven,
but also patient and caring. challenges the student just enough so that they move one step
further to unlock their potential.
```
Ses: `en-US-Nova:DragonHDLatestNeural` (fallback `en-US-Ava`). Çizimler: `rubie-lesson`, `rubie-card`.

Tutor değiştirme: `POST /api/session/{id}/tutor {tutorId, recompileInitiate}`; model yeni tutor'ı kısa bir cümleyle tanıtıp derse kaldığı yerden devam eder (istemci bu talimatı üretir):

> "The student has just switched tutors. You are now {yeni}, taking over this lesson from {eski}. Greet the student as {yeni} in one short sentence, then carry on exactly where the lesson stopped..."

---

## 11. API envanteri (özet)

Tam tablo: [`api-inventory.md`](api-inventory.md) · makine-okunur: [`endpoints.json`](endpoints.json)

| Grup | Öne çıkan endpoint'ler |
|---|---|
| Session | `POST /api/session/create`, `GET/PATCH/DELETE /api/session/{id}`, `GET /api/session/{id}/events`, `POST /api/session/generate-response`, `POST /api/session/collapse-context`, `POST /api/session/reset`, `POST /api/session/{id}/finish`, `.../heartbeat`, `.../clear-finish-reason`, `.../check-uploads`, `.../recompile-initiate-instructions`, `.../tutor`, `.../info`, `.../pdf`, `POST /api/session/create-title`, `PATCH /api/session/update-title`, `POST /api/session/fix-latex`, `POST /api/session/feedback`, `POST /api/session/signed-upload-urls`, `GET /api/session/list-user-sessions` |
| User | `POST /api/user/check-session-gating`, `GET/PATCH /api/user/settings`, `GET /api/user/tutor-models`, `POST /api/user/restore-subscription`, `POST /api/user/create-paddle-transaction`, `POST /api/user/create-phone-trial-transaction`, `POST /api/user/mobile-trial-started`, `POST /api/user/sync-locale-cookie`, `GET /api/user/academy` |
| Attachment | `POST /api/attachment/process`, `.../process-with-status-streaming`, `.../process-link-streaming`, `.../upload-link-thumbnail`, `.../update-prompt-suggestions`, `.../consolidate-images`, `.../proxy-download` |
| Voice | `POST /api/voice/get-token`, `POST /api/voice/get-tts`, `POST /api/voice/track-stt-session` |
| Diğer | `POST /api/client-log`, `GET /api/dialogs/active`, `POST /api/dialogs/{dismiss,complete}`, `GET /api/pricing/paddle-localized`, `GET /api/doc-repo/document-thumbnail/{slug}`, `GET /api/academy/...`, admin analytics/manual-intervention uçları |

---

## 12. Yeniden üretim (reimplementation) planı

Aynı ürünü sıfırdan kuracaksan minimum çekirdek:

### 12.1 Veri modeli

```ts
Session {
  id, userId, sessionType, title, tutorId,
  settings { language, speechSpeed },
  isFirstLesson, isDemoLesson, experiments[],
  promptVariables { tutorName, academyLessonId },
  fileUploads[], skills[], lessonPlanSlotTemplate,
  finishReason?, timeSpent?
}

Event {
  turnIndex, orderIndex, subOrderIndex, timestamp, name,
  elementId?, position?{spaceId,columnId}, contentType?, content?,
  text?, speechStyle?, actionItem?, collapseId?, ...
}
```

### 12.2 Sunucu

1. **Turn orchestrator**: gelen `delta` + geçmiş event'lerden model bağlamı kur; NDJSON stream döndür.
2. **Komut şeması**: yukarıdaki 13 komutu JSON Schema / tool calling ile zorla; her komuta monoton `orderIndex` ver.
3. **Kalıcılık**: event append-only tablo + `(turn,order,sub)` unique; `persistedCount` ile iyimser kilitleme.
4. **Bağlam sıkıştırma**: son N model turn'ü tut; ortadakileri özetle.
5. **RAG**: doküman/chunk tablosu + retrieval aracı; her erişimi event olarak logla.
6. **TTS proxy**: metin + voice + style → PCM stream + viseme/word boundary.
7. **STT token broker**: kısa ömürlü Deepgram/Azure/AssemblyAI token'ı.
8. **Gating servisi**: paid/daily/trial/hardPaywall durumlarını tek yerden döndür.

### 12.3 İstemci

1. NDJSON reader (satır bazlı, abort edilebilir).
2. `Map<orderIndex, handler>` + sıralı executor.
3. Event → UI reducer (tahta, altyazı, transcript, avatar).
4. Delta writer (409'da reload).
5. Ses pipeline (worklet → WS STT; TTS worker → AudioContext + viseme).
6. Review/preview/resume modları.

### 12.4 Taklit edilmeye değer küçük detaylar

- `actionItem.response` ile modelin araç varsayımını geri beslemek.
- İlerlemeli SVG (`_stream`) ile çizim "canlıymış" hissi.
- Silme animasyonunu geciktirip aynı element gelirse iptal etmek.
- LaTeX'i konuşma metnine çevirmek (KaTeX + speech-rule-engine).
- `x-tutor-ladder-source` gibi şeffaf yönlendirme başlığı.
- Model yanıtı NDJSON olunca araç çağrılarının sırayla ve kısmi uygulanabilmesi.

---

## 13. Bilinmeyenler / doğrulanması gerekenler

| Konu | Durum |
|---|---|
| Sistem prompt'u ve sokratik öğretim politikası | Sunucuda; istemcide yok |
| `generate-response` NDJSON örnek çerçeveleri | Canlı trafik yakalanmadı |
| `get-tts` istek/yanıt gövdesi | Worker chunk'ı (7488/5107) indirilmedi |
| Auth sağlayıcısı, cookie adı, oturum süresi | İstemcide SDK yok; middleware'de |
| Günlük limit / trial sayıları | Sunucu yapılandırması |
| Model ladder'ın tam sırası ve geo/plan kuralları | Yalnızca istemci kataloğu ve başlık etiketleri biliniyor |
| Admin/academy uçlarının yetki modeli | Kod var, çalışma zamanı yok |
| Rate limit, maliyet limitleri | Görünmüyor |

Bu maddeleri doğrulamak için sıradaki adım: REA'nın `observe-web-session` komutuyla kullanıcı etkileşimli bir pencere açıp **kendi oturumunuzda** bir tur atmak; ya da `capture-browser-scenario` ile kendi hesabınızda tek adımlık senaryo çalıştırmak.

---

## 14. Kanıt dosyaları

| Dosya | İçerik |
|---|---|
| `REPORT.md` | Bu rapor (ana deliverable) |
| `api-inventory.md` | 56 endpoint'in tam tablosu + ortak sözleşmeler + akış diyagramı |
| `endpoints.json` | Makine-okunur API, event, komut ve akış çerçevesi tanımları |
| `live-protocol.md` | Canlı doğrulanmış protokol: gerçek istek/yanıt gövdeleri, 88 akış çerçevesi |
| `dil-notlari.md` | Aynı oturumdan Almanca öğrenme notları |
| `rea-runs/studdyai/capture/live-turn-1.json` | CDP ağ kaydı: 246 istek, gövdeler dahil (5 dk) |
| `rea-runs/studdyai/capture/live-voice-1.json` | 2. tur: 243 istek, 41 TTS, 3 STT token, 312 WS çerçevesi (7 dk) |
| `rea-runs/studdyai/capture/live-turn-1.har.json` | Aynı kaydın HAR 1.2 projeksiyonu |
| `rea-runs/studdyai/capture/cdp-capture.mjs` | Kaydedici aracı (page + worker auto-attach) |
| `rea-runs/studdyai/worker-7488.pretty.js` | TTS worker kaynağı (beautified, 198 satır) — tel protokolü kanıtı |
| `rea-runs/studdyai/page-inspect.json` | Sayfa DOM/script/network/storage gözlemi |
| `rea-runs/studdyai/bundle.json` | Statik bundle analizi: 21 route adayı, 36 endpoint |
| `rea-runs/studdyai/page-with-sources.json` | 44 script kaynağı (3.8 MB) |
| `rea-runs/studdyai/scripts/` | Dışa aktarılmış scriptler + manifest (URL ↔ dosya) |
| `rea-runs/studdyai/tutor-page-app.json` | Tutor-session chunk'ının semantik grafiği (43 MB) |
| `rea-runs/studdyai/pretty/s3.js` | Turn motoru, session VM, gating, STT/TTS orkestrasyonu |
| `rea-runs/studdyai/pretty/s16.js` | Komut handler'ları, model kataloğu |
| `rea-runs/studdyai/pretty/s18.js` | Session API istemcisi, session listesi, admin uçları |
| `rea-runs/studdyai/pretty/s36.js` | TTS (Azure/OpenAI), tutor kataloğu, session entity |
| `rea-runs/studdyai/pretty/s40.js` | Deepgram / Azure / AssemblyAI STT |
| `rea-runs/studdyai/pretty/s8.js` | Home, create session, gating |
| `rea-runs/studdyai/pretty/s9.js` | Attachment upload/process |
| `rea-runs/studdyai/session.png` | Canlı ekran görüntüsü |
| `rea-runs/studdyai/screenshot.json` | PNG artifact + SHA-256 |

### Tekrar çalıştırma

```bash
# Chrome zaten açık olmalı:
# /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
#   --remote-debugging-port=9222 --user-data-dir=/tmp/chrome_debug_profile

cd rea-runs/studdyai
npx -y rea-agents@latest list-browser-targets http://127.0.0.1:9222 \
  --allowed-origins https://studdyai.com
npx -y rea-agents@latest analyze-web-bundle http://127.0.0.1:9222 <targetId> \
  --allowed-origins https://studdyai.com
```

---

## 15. Tek cümlelik tasarım içgörüsü

Studdy'nin asıl hilesi "AI tutor" olması değil; **model çıktısını bir olay günlüğüne ve komut akışına zorlaması**. Böylece aynı akış hem gerçek zamanlı tahtayı, hem sesi, hem de yeniden yüklenebilir ders kaydını üretiyor. Bu, klasik chat UI'ına göre çok daha yüksek mühendislik ama çok daha güçlü bir "ders" hissi veriyor.

---

## 16. Canlı doğrulama turu (28.10.2026)

İlk rapor statik bundle analizine dayanıyordu. İkinci turda kullanıcının açık oturumu CDP ile **pasif olarak dinlendi** (JS enjeksiyonu yok, istek sürükleme yok): 5 dakikada **246 istek**, 7 `generate-response` çağrısı, **88 akış çerçevesi**, 63 delta senkronu.

Tam protokol: [`live-protocol.md`](live-protocol.md) · Ham kayıt: `rea-runs/studdyai/capture/live-turn-1.json`

### 16.1 Doğrulananlar

| İddia | Sonuç |
|---|---|
| `generate-response` satır bazlı JSON akışı döner | ✅ 88 çerçeve |
| Komutlar `orderIndex` ile sıralı yürütülür | ✅ |
| `_stream:"svg"` kademeli çizim | ✅ tek eleman için 23 parça |
| `PATCH` delta + `persistedCount` | ✅ 63 çağrı, yanıt hep 36 byte |
| Gating 10 sn'de bir yoklanır | ✅ 31 çağrı / 5 dk |
| `draw` = görsel üretimi | ✅ Supabase public bucket'a PNG |
| Azure TTS | ✅ client-log `source:"AzureTTS"`, AudioContext 48 kHz |
| Cookie tabanlı auth | ✅ `Authorization` header'ı yok |

### 16.2 Düzeltilenler (ilk rapor yanlıştı)

| İlk iddia | Gerçek |
|---|---|
| `Content-Type: application/x-ndjson` | **`text/event-stream`** — gövde NDJSON ama SSE başlığı ile |
| Komut çerçevesi `{type:"speak",…}` | **`{orderIndex, commandName, actionItem{internalId,name,args}}`** + denormalize alanlar |
| `speak` alanları `speechStyle`/`speechStyleDegree` | **`style`** / **`styledegree`** |
| `x-tutor-ladder-source` her yanıtta | 7 yanıtta da **yok** — koşullu header |
| Event modeli 26 tip | **32 tip** (+`session_paused`, `session_unpaused`, `transcript_opened`, `transcript_closed`, `whiteboard_element_edited`, `session_resumed`) |
| Kota değerleri bilinmiyor | **2 ders/gün · 10 dk/ders · 15 dk deneme** |
| `draw` argümanı `svg`/`filePath` | **`prompt`** + `maxDimensions`; sunucu `maxDims`'a büyütüyor (528×288) |

### 16.3 Yeni keşifler

1. **Sistem prompt'u istemciden gidiyor.** Event `text` alanında `[SYSTEM] … [END SYSTEM]` sınırlayıcısıyla. Örn. `session_resumed`: *"The user has previously ended the tutoring session and now they are back to resume it. Greet them and ease into it."*
2. **`draw` iki fazlı.** Önce boş `whiteboard_element_added`, görsel hazır olunca `whiteboard_element_edited` ile Supabase URL'i.
3. **Supabase Storage sızıntısı.** `umcvdgstciybysimigxa.supabase.co/storage/v1/object/public/generated_images/<epoch_ms>-<elementId>.png` — public bucket, auth gerektirmiyor.
4. **Inactivity duraklatma ~66 sn.** `session_paused{reason:"inactivity"}` → `session_unpaused{reason:"pause_button"}`.
5. **Transkript paneli dersi duraklatıyor.** `transcript_opened` → `session_paused{reason:"transcript_open"}`; kapanınca `session_unpaused{reason:"transcript_close"}`.
6. **`subOrderIndex` çift görevli.** Komut içi parça sırası *ve* aynı `(turnIndex, orderIndex)` içindeki ardışık olay sayacı.
7. **Vercel topolojisi.** `x-vercel-id: fra1::iad1` → Frankfurt edge, US-East compute.
8. **`/api/user/settings` e-posta döndürüyor** — paywall ekranı bunu kullanıyor.

### 16.4 Hâlâ açık

- `collapse-context`: tur sayısı eşiğe ulaşmadı.
- Cookie adı: CDP değerleri göstermiyor.
- STT WebSocket host'u: token `region: eastus` diyor, ama `webSocketCreated` filtresi o host'u kapsamıyordu.
- `track-stt-session.closeReason` için `muted` dışında değer görülmedi.

> Yan ürün: [`dil-notlari.md`](dil-notlari.md) — aynı oturumun Almanca öğrenme notları (sözlük, kalıplar, kendi hatalarının düzeltmesi).

---

## 17. Ses hattı doğrulaması (2. tur)

İkinci kayıt (7 dk, oturum 364614) worker attach ile yapıldı: **243 istek, 312 WebSocket çerçevesi, 4 child target** (1 TTS worker + 3 AudioWorklet).

### 17.1 TTS — kesin

- `content-type: application/x-tts-framed-stream`, `cache-control: no-store`.
- 41 çağrı; hepsi **`de-DE-Florian:DragonHDLatestNeural`**, `model-3`, 24 kHz.
- İstekte `locale`, `style`, `styleDegree`, `prebufferMs` **yok** — dil `voice` adından çıkarsanıyor.
- Tel formatı: `[type:u8][length:u32 LE][payload]`; sunucu **50 ms / 2400 bayt** çerçeveler gönderiyor, istemci 80 ms'e yeniden bölüyor.
- Preroll 30720 bayt (640 ms) — ilk ses gecikmesini gizleyen hile.
- Ses boyutu 143–588 KB, sentez süresi 1,1–3,7 sn (medyan 2,2 sn).

### 17.2 STT — kesin

- `tokenId: "token-3"` → Azure Speech JWT (`region: eastus`, `SpeechServices.S0`), kısa ömürlü.
- **Azure Speech SDK 1.47.0** doğrudan konuşuyor; ara katman yok.
- `speech.config` → `recognition:"conversation"`, 16 kHz/16-bit/mono PushStream.
- `speech.context` → `phraseDetection{Conversation, segmentationSilenceTimeoutMs:1000}`.
- `PrimaryLanguage.language = de-DE` sunucudan geliyor → dil otomatik algılanıyor.
- Kapanış: `track-stt-session{provider:"azure", model:"azure-stt", closeReason:"muted", audioDurationSeconds, sessionDurationSeconds}` — **süre istemci tarafından bildiriliyor**, yani faturalama istemciye güveniyor.

### 17.3 Bu turda kapanan boşluklar

| Önceki boşluk | Durum |
|---|---|
| `get-tts` gövdesi | ✅ 41 gerçek istek |
| TTS tel formatı | ✅ worker kaynağı + canlı baytlar |
| `get-token` | ✅ `tokenId:"token-3"` |
| STT sağlayıcı/protokol | ✅ Azure SDK 1.47.0 |
| Ses kalitesi/gecikme | ✅ ölçüldü |
