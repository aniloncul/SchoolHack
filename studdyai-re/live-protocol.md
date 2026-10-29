# Canlı Doğrulanmış Protokol — Studdy AI

Kaynak: 28.10.2026 canlı oturum, CDP ağ kaydı (246 istek, 7 `generate-response` çağrısı, 88 akış çerçevesi, 63 delta senkronu).
Ham kanıt: `rea-runs/studdyai/capture/live-turn-1.json` (+ `.har.json`).

> Bu dosya **yalnızca gerçekten gözlenen** şeyleri içerir. Kod okumasından çıkan ama doğrulanmayan iddialar `REPORT.md §16`'da ayrıca işaretlenmiştir.

---

## 1. `POST /api/session/generate-response`

### İstek

```
POST https://studdyai.com/api/session/generate-response
content-type: application/json
x-deployment-id: dpl_AzRgfhPtbqpxN7qHiztsUnL2WLc5
referer: https://studdyai.com/tutor-session/364562
```

```json
{
  "sessionId": 364562,
  "delta": {
    "appended": [
      {
        "timestamp": "2026-10-28T12:27:21.789Z",
        "turnIndex": 4,
        "orderIndex": 0,
        "subOrderIndex": 0,
        "name": "message_sent_by_user",
        "text": "mol rechnen und es it morgern",
        "type": "chat"
      }
    ],
    "removed": []
  }
}
```

- `delta.appended` her turda **tek** event taşıyor (kullanıcı mesajı veya sistem event'i).
- `turnIndex` çift sayılarla artıyor (2, 4, 6, 8…) — tek sayılar tutor turuna ait.
- `type: "chat"` yalnızca kullanıcı mesajlarında görüldü.

### Yanıt

```
HTTP/2 200
content-type: text/event-stream
cache-control: no-cache
x-matched-path: /api/session/generate-response
x-vercel-cache: MISS
x-vercel-id: fra1::iad1::lc7kg-...
server: Vercel
```

**Kritik:** Başlık `text/event-stream`, ama gövde **SSE formatında değil** — `data:` öneki yok, düz **NDJSON** (satır başına bir JSON nesnesi). Yani SSE başlığı sadece "streaming + no-cache" semantiği için kullanılıyor, tarayıcı `EventSource` ile değil `fetch` + `ReadableStream` ile okuyor.

```
{"orderIndex":0,"actionItem":{...},"commandName":"speak","text":"..."}
{"orderIndex":1,"actionItem":{...},"commandName":"circle_annotate","annotationId":"...","targetElementId":"...","color":"blue"}
{"_stream":"svg","elementId":"formula_triangle","chunk":"<svg xmlns=...>"}
...
```

### 1.1 Komut çerçevesi (tam şema)

```ts
type TutorCommandFrame = {
  orderIndex: number
  commandName: 'speak' | 'write_text' | 'write_latex' | 'draw'
             | 'highlight' | 'circle_annotate' | 'erase' | ...

  actionItem: {                    // ← modelin ham çıktısı, olduğu gibi
    internalId: string             // uuid v4, idempotency anahtarı
    name: string                   // commandName ile aynı
    args: Record<string, unknown>  // komut argümanları
  }

  // ↓ denormalize edilmiş kopyalar (istemci doğrudan bunları okuyor)
  text?: string
  elementId?: string
  position?: { spaceId: string, columnId: string | number }
  annotationId?: string
  targetElementId?: string
  color?: string
  textToMatch?: string
  latex?: string
  scaleFactor?: number
  generationState?: 'generation'
  maxDims?: { maxWidth: number, maxHeight: number }
  isCoordinatePlane?: boolean
  fileType?: 'png'
  filePath?: string
  svgGenerationVariant?: string
}
```

Gözlenen 23 farklı üst düzey anahtar: `_stream, actionItem, annotationId, chunk, color, commandName, elementId, filePath, fileType, generationState, isCoordinatePlane, latex, maxDims, orderIndex, position, scaleFactor, svgGenerationVariant, targetElementId, text, textToMatch`.

### 1.2 Gözlenen komutlar (canlı)

| Komut | Örnek args | Not |
|---|---|---|
| `speak` | `{type, text, style:"chat"\|"friendly", styledegree?}` | `styledegree` yalnızca `friendly` stilde |
| `write_text` | `{type, id, text:"<h1>…</h1><br>…", position:{spaceId,columnId}}` | HTML string |
| `write_latex` | `{type, id, latex, color:"#222222", position}` | frame'de `scaleFactor` da var |
| `draw` | `{type, id, prompt, isStructured:false, maxDimensions:{maxWidth,maxHeight}, position}` | frame'de `generationState:"generation"`, `fileType:"png"`, `maxDims` (sunucu büyütür) |
| `highlight` | `{type, id, targetElementId, color:"yellow", textToMatch}` | |
| `circle_annotate` | `{type, id, targetElementId, color:"blue"\|"red", textToMatch?}` | |

**Sunucu `maxDimensions`'ı büyütüyor:** istekte `{maxWidth:300, maxHeight:250}` → frame'de `maxDims:{maxWidth:528, maxHeight:288}`. Yani nihai görsel boyutu sunucu politikası belirliyor, model değil.

### 1.3 Kademeli SVG akışı

```json
{"_stream":"svg","elementId":"formula_triangle","chunk":"<svg xmlns=\"...\" width=\"200\" ...>"}
{"_stream":"svg","elementId":"formula_triangle","chunk":"  <g stroke=\"#ABABAB\" ...>"}
{"_stream":"svg","elementId":"formula_triangle","chunk":"linien\">\n    <path d=\"M57"}
```

- Tek eleman için **23 parça** gözlendi (~40–80 karakterlik dilimler).
- `chunk` ham SVG metni; istemci birleştirip `innerHTML` ile basıyor.
- Not: `className=` özniteliği SVG çıktısında var (React style değil, ham string).

---

## 2. Delta senkronizasyonu — gerçek akış

Her komut yürütüldükten **sonra** istemci tek bir PATCH atıyor:

```
PATCH /api/session/364562
{"appended":[<event>],"removed":[],"persistedCount":23}

→ 200 {"persistedCount":24}
```

Gözlenen: **63 PATCH**, her biri ortalama **1 event**, yanıt hep **36 byte** (`{"persistedCount":N}`).
→ Yani event-per-action; toplu gönderim yok. Tur başına 6–9 PATCH.

### 2.1 Event zarfı — gerçek örnekler

**Tutor konuşması** (komut yürütüldükten sonra `response` damgalanır):

```json
{
  "timestamp": "2026-10-28T12:27:00.628Z",
  "turnIndex": 3,
  "orderIndex": 0,
  "subOrderIndex": 0,
  "name": "message_received_from_tutor",
  "text": "Hey …, schön, dass du wieder da bist! Wir waren gerade am Anfang.",
  "actionItem": {
    "internalId": "66aef676-7a41-467d-ab1f-4e06c18ca271",
    "name": "speak",
    "args": { "type": "speak", "text": "…", "style": "friendly", "styledegree": 0.8 },
    "response": { "success": true }
  }
}
```

**Tahtaya eleman ekleme:**

```json
{
  "name": "whiteboard_element_added",
  "elementId": "student_answers",
  "position": { "spaceId": "session_setup", "columnId": "overview" },
  "contentType": "html",
  "content": "<b>Deine Antworten:</b><br>Thema: Rechnen mit dem Mol<br>Prüfung: morgen",
  "turnIndex": 5, "orderIndex": 0, "subOrderIndex": 0,
  "actionItem": { "internalId": "...", "name": "write_text", "args": {...}, "response": {"success": true, "id": "student_answers"} }
}
```

**Anotasyon:**

```json
{
  "name": "whiteboard_annotation_added",
  "annotationId": "circle_context_resume",
  "targetElementId": "context_questions",
  "annotationType": "circle",
  "color": "blue",
  "actionItem": { "name": "circle_annotate", "response": {"success": true, "id": "circle_context_resume"} }
}
```

`annotationType`: `circle` | `highlight`. `color`: `blue` | `red` | `yellow`.

**Görsel üretimi (iki fazlı!):**

```json
// faz 1 — komut anında, içerik henüz yok
{ "name": "whiteboard_element_added", "elementId": "mol_analogy",
  "contentType": "image-file", "content": "",
  "size": {"width": 528, "height": 288}, "isCoordinatePlane": false,
  "actionItem": {"name":"draw","response":{"success":true,"id":"mol_analogy"}} }

// faz 2 — görsel hazır olunca AYNI element düzenlenir
{ "name": "whiteboard_element_edited", "elementId": "mol_analogy",
  "contentType": "image-file",
  "content": "https://umcvdgstciybysimigxa.supabase.co/storage/v1/object/public/generated_images/1793190500231-mol_analogy.png",
  "size": {"width": 528, "height": 288} }
```

> **Altyapı sızıntısı:** görseller **Supabase Storage**'da public bucket'ta: `umcvdgstciybysimigxa.supabase.co/storage/v1/object/public/generated_images/<epoch_ms>-<elementId>.png`. İstemci görüntüyü yine de `https://studdyai.com/<uuid>` üzerinden yüklüyor (1440 px genişlik, 550–780 KB PNG) — yani bir proxy/rewrite var.

---

## 3. Sistem talimatları istemciden gidiyor

`generate-response` isteğinin içinde, event `text` alanında **sistem prompt'u** taşınıyor:

```json
{
  "name": "session_resumed",
  "text": "[SYSTEM] The user has previously ended the tutoring session and now they are back to resume it. Greet them and ease into it. [END SYSTEM]"
}
```

Bu, mimarinin en önemli detaylarından biri: istemci, sunucuya "ne yapması gerektiğini" event metni içinde `[SYSTEM] … [END SYSTEM]` sınırlayıcısıyla söylüyor. Yani sistem prompt'u istemci tarafında kuruluyor ve **kullanıcı tarafından değiştirilebilir bir yüzey**.

---

## 4. Gating — gerçek yanıt

```
POST /api/user/check-session-gating   {"sessionId":364562}
```

```json
{
  "status": "success",
  "isPaid": false,
  "isSponsored": false,
  "dailyValidSessionsCount": 1,
  "lifetimeValidSessionsCount": 5,
  "isTrialExhausted": true,
  "isDailySessionCountExceeded": false,
  "isSessionDurationExceeded": false,
  "isPreviousDaySessionBlocked": false,
  "isAtDailySessionLimit": false,
  "sessionIncludedInTrial": false,
  "numNonTrialedSessionsDoneToday": 1,
  "hardPaywallEnabled": false,
  "softFeatureGateEnabled": true,
  "limits": {
    "freeTrialMinutes": 15,
    "dailySessionCountLimit": 2,
    "perSessionTimeLimitMinutes": 10
  },
  "stopReason": null
}
```

**Gerçek limitler:** günlük **2** ders · ders başına **10 dk** · toplam ücretsiz deneme **15 dk**.
`isSponsored` ve `softFeatureGateEnabled` kodda yoktu — canlıda çıktı. Kadans: **10 saniye** (5 dk'da 31 çağrı).

---

## 5. Diğer doğrulanmış gövdeler

| Uç | Gerçek yanıt |
|---|---|
| `GET /api/user/settings` | `{"status":"success","settings":{"profile":{"providedName":"…","gradeLevel":"High School"},"preferences":{"preferredLanguage":"de","micEnabledByDefault":false,"preferredSpeechSpeed":1,"preferredTutor":"alfrenzo","heardAboutUs":"other","heardAboutUsOther":null},"email":"…"}}` |
| `GET /api/user/tutor-models` | `{"models":[],"admin":false}` |
| `GET /api/dialogs/active` | `{"dialog":null}` |
| `POST /api/session/{id}/heartbeat` | `{"success":true}` |
| `POST /api/session/{id}/clear-finish-reason` | `{"success":true}` |
| `GET /api/session/{id}/events` | `{"events":[…22 event…],"total":22}` |
| `POST /api/client-log` | `{"success":true}` |
| `POST /api/session/{id}/finish` | 200, gövde yok — `sendBeacon` (`type: Ping`), URL'de `?dpl=…` |

`/api/user/settings` içinde **e-posta** dönüyor — istemci bunu gating/paywall ekranında gösteriyor.

### 5.1 Client-log şeması

```json
{
  "events": [{
    "source": "AzureTTS",
    "level": "info",
    "message": "AudioContext created",
    "payload": { "state": "running", "sampleRate": 48000 },
    "online": true,
    "effectiveType": "4g",
    "visibility": "visible",
    "sinceLoadMs": 2187,
    "session": "364562"
  }]
}
```

`source` değerleri: `AzureTTS`, … → **TTS sağlayıcısı canlıda Azure**, doğrulandı.

---

## 6. Event modeli — canlıda görülen 32 tip

Kodda 26 tip vardı; canlıda **6 yeni tip** çıktı:

| Yeni tip | Alanlar | Anlam |
|---|---|---|
| `session_paused` | `reason` | `inactivity` \| `transcript_open` |
| `session_unpaused` | `reason` | `pause_button` \| `transcript_close` |
| `transcript_opened` | — | Transkript paneli açıldı |
| `transcript_closed` | `source` | `user` |
| `whiteboard_element_edited` | `content`, `size` | Görsel üretimi tamamlandı |
| `session_resumed` | `text` | `[SYSTEM]` talimatı ile dönüş |

**Inactivity eşiği:** son aktivite 12:06:33 → `session_paused` 12:07:39 = **~66 saniye**. (Tahmini 60–90 sn.)

**`subOrderIndex` kullanımı:** tahta olayları dışındaki (pause/unpause/transcript) event'ler **aynı `turnIndex`+`orderIndex`** içinde `subOrderIndex`'i 1,2,3… diye artırıyor. Yani `subOrderIndex` sadece komut içi parça değil, "aynı konumdaki ardışık olay" sayacı.

---

## 7. Zamanlama (gerçek)

| Ölçüm | Değer |
|---|---|
| `generate-response` akış süresi | 2,1 s – 11,5 s (7 çağrı) |
| Tur başına PATCH | 6–9 |
| PATCH gecikmesi | 320–1020 ms |
| Heartbeat | 10 s |
| Gating yoklaması | 10 s |
| Inactivity duraklatma | ~66 s |
| Delta `409` | bu oturumda **hiç** görülmedi |

`draw` içeren turlar en uzun (11,5 s) — görsel üretimi akışı açık tutuyor.

---

## 8. Hâlâ açık

| Konu | Durum |
|---|---|
| `get-token` / `get-tts` gövdeleri | ✅ **doğrulandı** — §9 |
| STT akışı | ✅ **doğrulandı** — Azure Speech SDK 1.47.0, §10 |
| `collapse-context` | ❌ tur sayısı eşiğe ulaşmadı |
| Gerçek auth cookie adı | ❌ CDP cookie değerlerini göstermiyor; `Authorization` header'ı yok → cookie tabanlı olduğu kesin, adı belirsiz |
| `x-tutor-ladder-source` | ❌ 16 yanıtta da yok |
| STT WebSocket URL'i | ❌ `webSocketCreated` filtresi bu host'u kapsamıyordu; token `region: eastus` diyor → `wss://eastus.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1` olması beklenir (doğrulanmadı) |
| `closeReason` diğer değerleri | ❌ yalnızca `muted` görüldü |

---

## 9. TTS worker — tam protokol

İlk turda "indirilemedi" denen modül bulundu: `_next/static/chunks/7488.016d207a7f1f7f57.js` (3,4 KB). Worker olarak çalışıyor, ana thread'den `postMessage` ile besleniyor.

### 9.1 Ana thread → worker

| Mesaj | Alanlar | Anlam |
|---|---|---|
| `process` | `{taskId, text, config}` | Sıraya sentez görevi ekle |
| `ready-for-next` | — | Önceki ses çalındı, sıradakine geç |
| `abort` | `{taskId}` | Görevi iptal et (fetch `AbortController` ile) |

`config`: `{apiPath, voice, locale, sampleRate, speed, model, prebufferMs, style, styleDegree, includeTimestamps, turnIndex, actionInternalId, sessionId, userId, clientId}`

### 9.2 Worker → ana thread

| Mesaj | Alanlar |
|---|---|
| `chunk` | `{taskId, float32}` — **transferable**, kopyasız |
| `viseme` | `{taskId, data}` |
| `word-boundary` | `{taskId, data}` |
| `task-complete` | `{taskId}` |
| `done` | `{taskId, trailingFrameBytes}` |
| `stall` | `{taskId, msSinceLastByte, bytesReceived, elapsedMs}` |
| `error` | `{taskId, error, phase:"fetch"\|"stream", httpStatus, bytesReceived, elapsedMs}` |

### 9.3 HTTP isteği (kesin gövde)

**Gerçek istek** (41 çağrının tamamı aynı şekilde):

```json
POST /api/voice/get-tts?dpl=dpl_...
Content-Type: application/json

{
  "model": "model-3",
  "text": "Nur der Artikel arbeitet. Der Tisch selbst chillt einfach.",
  "voice": "de-DE-Florian:DragonHDLatestNeural",
  "sampleRate": 24000,
  "speed": 1,
  "responseFormat": "pcm",
  "includeTimestamps": true,
  "sessionId": "364614",
  "userId": "…",
  "clientId": "studdy",
  "turnIndex": 3,
  "actionInternalId": "a74314df-88e1-4125-ba9e-357aaf0027b4"
}
```

```
HTTP/2 200
content-type: application/x-tts-framed-stream
cache-control: no-store
```

> **Düzeltme:** `locale`, `style`, `styleDegree`, `prebufferMs` gerçek istekte **gönderilmiyor**. `locale` yerine dil `voice` adından çıkarsanıyor (`de-DE-…` → Almanca, `en-US-…` → İngilizce). `prebufferMs` yalnızca istemcide preroll hesabı için; `style`/`styleDegree` yalnızca belirli konuşmalarda ekleniyor.
> `sessionId` burada **string**, delta isteklerinde **number**.

### 9.4 Yanıt: 5 baytlık çerçeve başlığı

```
[0]      type  : u8     (0=PCM, 1=viseme JSON, 2=word-boundary JSON)
[1..4]   length: u32 LE
[5..]    payload
```

- **type 0** → PCM Int16 LE. İstemci `Float32Array`'e çeviriyor (`sample / 32768`) ve transferable olarak UI thread'ine atıyor.
- **type 1/2** → UTF-8 JSON.
- **Sunucu 50 ms'lik çerçeveler gönderiyor:** ilk çerçeve `0x0960` = **2400 bayt** = 1200 örnek @ 24 kHz = 50 ms. İstemci bunları 80 ms'lik (3840 bayt) çerçevelere yeniden bölüyor.

**Preroll (istemci tarafı):** `2 × 24000 × 0.64` = **30720 bayt** dolana kadar çalmıyor.

**Ölçülen performans (41 çağrı):**

| Metrik | Değer |
|---|---|
| Metin | medyan 99 karakter (maks 169) |
| Ses boyutu | 143 KB – 588 KB |
| İlk bayt → bitiş | 1,1 s – 3,7 s |
| Ses | `de-DE-Florian:DragonHDLatestNeural` (41/41) |
| Model | `model-3` (41/41) |
| sampleRate / speed | 24000 / 1 (41/41) |

**Takılma dedektörü:** 5 sn'de bir kontrol; 20 sn boyunca bayt gelmezse `stall` mesajı. Hata mesajı `phase` alanıyla "fetch mi stream mi" ayrımını yapıyor — iyi bir hata ayıklama tasarımı.

**Eşzamanlılık:** Worker tek görev işliyor; kuyrukta iş varsa `task-complete` sonrası ana thread'den `ready-for-next` bekliyor (ses çalma bitince). Yani sentez, oynatmaya göre önden koşuyor ama en fazla bir görev.

---

## 10. STT — Azure Speech SDK (kesin)

Mikrofon 3 kez kullanıldı; hat tamamen yakalandı.

```
POST /api/voice/get-token   {"tokenId":"token-3"}
→ 200 {"token":"<Azure Speech JWT>"}   // region: eastus, product: SpeechServices.S0
```

JWT içeriği (base64 çözülmüş): `region: eastus`, `subscription-id`, `cognitive-services-endpoint: https://api.cognitiveservices.microsoft.com/internal/v1.0/`, `azure-resource-id`. **Kısa ömürlü** — istemciye verilen tek yetki bu.

### 10.1 WebSocket el sıkışması

Azure Speech SDK **1.47.0** (JavaScript) doğrudan konuşuyor; arada kendi protokol katmanı yok:

```
Path: speech.config
{"context":{"system":{"name":"SpeechSDK","version":"1.47.0","build":"JavaScript","lang":"JavaScript"},
 "os":{"platform":"Browser/MacIntel",...},
 "audio":{"source":{"bitspersample":16,"channelcount":1,"model":"PushStream","samplerate":16000,"type":"Stream"}}},
 "recognition":"conversation"}

Path: speech.context
{"phraseDetection":{"mode":"Conversation",
  "conversation":{"segmentation":{"mode":"Custom","segmentationSilenceTimeoutMs":1000}}}}
```

- **16 kHz / 16-bit / mono**, PushStream ile besleniyor.
- Sessizlik eşiği **1000 ms** → konuşma biter, cümle gönderilir.
- Ses, ikili WebSocket çerçevesi olarak gidiyor: `Content-Type: audio/x-wav`, RIFF başlıklı parçalar.

### 10.2 Gelen olaylar

| `Path` | İçerik |
|---|---|
| `turn.start` | `{"context":{"serviceTag":"…"}}` |
| `speech.startDetected` | `{"Offset":78600000}` (100 ns birim) |
| `speech.hypothesis` | `{"Id","Text","Offset","Duration","PrimaryLanguage":{"Language":"de-DE"},"Channel"}` |
| `speech.endDetected` | `{"Offset"}` |
| `speech.phrase` | `{"Id","RecognitionStatus":"Success\|EndOfDictation","DisplayText","Offset","Duration"}` |

`PrimaryLanguage` sunucudan geliyor (`de-DE`) — yani dil istemciden zorlanmıyor, Azure otomatik algılıyor.

### 10.3 Kapanış telemetrisi

```json
POST /api/voice/track-stt-session
{
  "sessionId": "364614",
  "userId": "…",
  "clientId": "studdy",
  "provider": "azure",
  "model": "azure-stt",
  "closeReason": "muted",
  "audioDurationSeconds": 17.024,
  "sessionDurationSeconds": 17.024
}
→ {"success":true}
```

`closeReason`: `muted` | (muhtemelen `silence`, `navigated`, `error`). Faturalama bu çağrıya dayanıyor — ses süresi istemci tarafından bildiriliyor, yani **istemciye güvenen** bir ölçüm.

> **Not:** Statik analizde "İngilizce → Deepgram, diğer diller → Azure" varsayımı vardı. Almanca oturumda **Azure** kullanıldı — varsayım doğrulandı. `clientId` her yerde sabit `"studdy"`.
