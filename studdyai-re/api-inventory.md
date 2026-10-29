# Studdy AI — API Envanteri

Hedef: `https://studdyai.com/tutor-session/364562`
Origin: `https://studdyai.com` · Deployment: `dpl_AzRgfhPtbqpxN7qHiztsUnL2WLc5`
Kanıt tarihi: 2026-10-28 · Kaynak: statik bundle analizi (44 chunk)

> **Kanıt seviyesi notu:** Bu tablo istemci kodundan (fetch çağrıları, tip tanımları, sabitler) çıkarılmıştır. Canlı istek/yanıt gövdeleri yakalanmamıştır; alan adları ve şemalar kod okumasına dayanır. `pretty/*.js:satır` referansları ilgili kaynak dosyada çağrının kurulduğu yeri gösterir.

Makine-okunur sürüm: [`endpoints.json`](endpoints.json) · Canlı doğrulanmış protokol: [`live-protocol.md`](live-protocol.md)

---

## 0. Ortak sözleşmeler

| Konu | Gözlem |
|---|---|
| Base URL | Aynı origin, göreli `/api/...` yolları (ayrı API host'u yok) |
| Auth | İstemcide Supabase/Firebase/Clerk/Auth0/NextAuth SDK'sı **yok**. Oturum sunucu tarafında (HttpOnly cookie + middleware) kuruluyor. `__session` adı zayıf sinyal — kanıtlanmadı. |
| İçerik tipi | JSON; `generate-response` yanıtı **`text/event-stream`** başlığıyla geliyor ama gövde **düz NDJSON** (`data:` öneki yok) |
| Akış | `generate-response` (doğrulandı), `process-with-status-streaming`, `admin/analytics/sessions-data` |
| Deployment | Her istekte `x-deployment-id: dpl_AzRgfhPtbqpxN7qHiztsUnL2WLc5` |
| Edge | Vercel: `x-vercel-id: fra1::iad1` (Frankfurt edge, US-East compute) |
| Hata | `409` → delta çakışması, istemci sayfayı yeniler. Diğer hatalar `action_failed` / `command_failed` event'ine dönüşür. |
| Telemetri | `POST /api/client-log` neredeyse her chunk'ta; hata ve yaşam döngüsü olayları buradan gider |
| Idempotency | Görünür bir `Idempotency-Key` yok; tekrar koruması `persistedCount` + 409 ile sağlanıyor |

### Özel header'lar

| Header | Yön | Değerler | Anlam |
|---|---|---|---|
| `x-tutor-ladder-source` | response | `admin` \| `granted` \| `demo` \| `opus_start` \| `ops` | Hangi model merdiveni kuralının uygulandığı |

---

## 1. Session (oturum) — 22 uç

| # | Method | Path | Amaç | İstek | Yanıt | Kanıt |
|---|---|---|---|---|---|---|
| 1 | POST | `/api/session/create` | Yeni ders oturumu | `{sessionType: topic\|homework\|exam, userTextInput, attachments: FileUpload[], lifetimeValidSessionsCount, studentNameOverride, metadata:{appVersion:"1.0.7", boardDimensions:{width,height}}, academyLessonToken, denseCoverage:{arm:"dense"\|"control", consolidation?}}` | `{sessionId: number}` | `s8.js:934`, `s18.js:4566` |
| 2 | GET | `/api/session/{id}` | Oturum başlığı + durum | — | `{sessionHead, finishReason?, title?, timeSpent?}` | `s18.js:4590`, `s3.js:1038` |
| 3 | GET | `/api/session/{id}/events` | Event log sayfalama | `?from=<n>&limit=<n>` (limit 1000) | `{events: SessionEvent[], total: number}` | `s18.js:4598` |
| 4 | PATCH | `/api/session/{id}` | Delta senkronizasyonu | `{appended: SessionEvent[], removed: EventPosition[], head?, persistedCount}` | `{persistedCount}` · `409` → reload | `s18.js:4623`, `s3.js:3273` |
| 5 | DELETE | `/api/session/{id}` | Oturumu sil | — | — | `s18.js:4782` |
| 6 | POST | `/api/session/generate-response` | **Tur motoru** | `{sessionId, delta:{appended, removed, head?}, tutorModel?}` | NDJSON: `TutorCommand` \| `StreamPayload` | `s3.js:8762`, `s16.js:850` |
| 7 | POST | `/api/session/collapse-context` | Uzun bağlamı özetle | `{sessionId, delta}` | `{segments:[{summaryText, fromTurn, toTurn}]}` | `s3.js:8792`, `s3.js:893` |
| 8 | POST | `/api/session/reset` | Dersi sıfırla (yeni kimlik) | `{originalSessionId}` | `{newSessionId}` | `s18.js:4647` |
| 9 | POST | `/api/session/{id}/finish` | Dersi bitir | `{reason: user_ended\|completed\|paywalled\|reset\|abandoned, timeSpent}` · `abandoned` → `sendBeacon` | — | `s18.js:4664` |
| 10 | POST | `/api/session/{id}/clear-finish-reason` | Bitirme nedenini temizle (devam) | — | — | `s18.js:4683` |
| 11 | POST | `/api/session/{id}/heartbeat` | Canlılık + süre | `{timeSpent}` · **10 sn** kadans | — | `s18.js:4692`, `s3.js:1148` |
| 12 | POST | `/api/session/create-title` | Otomatik başlık üret | `{sessionId}` | `{title}` | `s18.js:4704` |
| 13 | PATCH | `/api/session/update-title` | Başlığı elle değiştir | `{sessionId, title}` | — | `s18.js:4769` |
| 14 | POST | `/api/session/{id}/check-uploads` | Yükleme uygunluk kontrolü | `{typedText, fileUploads}` | — | `s18.js:4717` |
| 15 | POST | `/api/session/{id}/recompile-initiate-instructions` | Başlangıç talimatını yeniden derle (dosya değişti) | `{fileUploads, promptVariables}` | — | `s18.js:4731` |
| 16 | POST | `/api/session/{id}/tutor` | Tutor değiştir | `{tutorId: "alfrenzo"\|"rubie", recompileInitiate}` | — | `s18.js:4745` |
| 17 | GET | `/api/session/list-user-sessions` | Geçmiş dersler (keyset paging) | `?limit&offset&sortOrder&updatedAfter&anchorUpdatedAt` | oturum listesi | `s18.js:4759` |
| 18 | GET | `/api/session/{id}/info` | Oturum meta bilgisi | — | — | `s18.js:4788` |
| 19 | POST | `/api/session/{id}/pdf` | Ders dökümünü PDF'e çevir | `{html}` | `application/pdf` blob | `s18.js:4793` |
| 20 | POST | `/api/session/fix-latex` | Bozuk LaTeX'i onar | `{latex, error, sessionId, userId?, turnIndex?}` | `{success, fixedLatex, error?}` | `s3.js:13` |
| 21 | POST | `/api/session/signed-upload-urls` | S3/R2 imzalı yükleme URL'i | `{files:[{fileName, mimeType}], sessionId?}` | `{uploads:[{storagePath, signedUrl, thumbnailStoragePath, thumbnailSignedUrl}]}` | `s9.js:702` |

Ek: `POST /api/session/feedback` — ders sonu geri bildirim (`s17.js`, `s18.js`).

### 1.1 Delta senkronizasyonu (kritik tasarım)

İstemci event log'un tek sahibi; sunucu yalnızca **fark** alıyor:

```ts
Delta = {
  appended: Event[],        // yeni event'ler
  removed:  EventPosition[],// {spaceId, columnId} silinen konumlar
  head?:    object          // oturum başlığı değiştiyse
}
```

`persistedCount` istemcinin bildirdiği "sen son kaç event'i gördün" sayacı. Sunucu bunu kendi sayacıyla karşılaştırıp uyuşmazsa `409` döner ve istemci sayfayı baştan yükler. Bu, çok sekemeli / çok cihazlı düzenlemelerde son-yazan-kazanır çakışmasını bilinçli olarak "tam yeniden yükleme"ye indirger.

### 1.2 `generate-response` akış sözleşmesi

```
POST /api/session/generate-response
Content-Type: application/json

{ "sessionId": 364562, "delta": { "appended": [...], "removed": [] } }

→ 200 text/event-stream  (gövde: NDJSON, `data:` öneki YOK)
   {"orderIndex":0,"actionItem":{"internalId":"…","name":"speak","args":{…}},"commandName":"speak","text":"…"}
   {"orderIndex":1,"actionItem":{…},"commandName":"circle_annotate","annotationId":"…","targetElementId":"…","color":"blue"}
   {"_stream":"svg","elementId":"el_3","chunk":"M 10 20 L …"}
   {"orderIndex":2,"actionItem":{…},"commandName":"draw",…}
```

- Her satır bağımsız bir JSON nesnesi; çerçeve = `{orderIndex, commandName, actionItem{internalId,name,args}}` + denormalize kopya alanlar.
- Komutlar `orderIndex` ile arabelleğe alınır, **sırayla** yürütülür. `subOrderIndex` aynı komut içindeki parça sırasını belirler.
- `_stream: "svg"` çerçeveleri, komut tamamlanmadan çizimi kademeli gösterir (tek eleman için ~23 parça gözlendi).
- Akış ortasında hata → `command_failed` (istemci tarafı) veya `action_failed` (event olarak).
- Gerçek çerçeveler: [`live-protocol.md`](live-protocol.md)

### 1.3 Yürütülen komutlar (14)

| Komut | Etki |
|---|---|
| `speak` | TTS kuyruğuna metin + stil ekle |
| `write_latex` | Beyaz tahtaya LaTeX yaz |
| `write_text` | Beyaz tahtaya düz metin yaz |
| `draw` | SVG şekil çiz |
| `erase` | Element/alan sil |
| `highlight` | Vurgu ekle |
| `circle_annotate` | Daire içine al + not |
| `finish_tutoring_lesson` | Dersi bitir (tetikleyici) |
| `document_retrieved` | RAG: doküman getirildi (görsel/atıf) |
| `chunk_retrieved` | RAG: chunk getirildi |
| `collapsed_block_expanded` | Özet bloğu aç |
| `skill_loaded` | Skill yüklendi |
| `lesson_plan_updated` | Ders planı güncellendi |
| `command_failed` | Komut başarısız |

---

## 2. User — 10 uç

| # | Method | Path | Amaç | İstek | Yanıt | Kanıt |
|---|---|---|---|---|---|---|
| 1 | POST | `/api/user/check-session-gating` | **Paywall/trial kapısı** | `{sessionId?}` | `{status, isPaid, isAtDailySessionLimit, isTrialExhausted, hardPaywallEnabled, lifetimeValidSessionsCount, stopReason, metrics}` | `s8.js:73`, `s3.js:2701` |
| 2 | GET | `/api/user/settings` | Kullanıcı profili + tercihler | — | `{status, settings:{profile:{providedName, gradeLevel}, preferences:{preferredLanguage, micEnabledByDefault, preferredSpeechSpeed, preferredTutor}}}` | `s3.js:2747` |
| 3 | PATCH | `/api/user/settings` | Tercihleri kaydet | `{preferences}` | — | `s3.js:2775` |
| 4 | GET | `/api/user/tutor-models` | Kullanıcının erişebildiği modeller | — | `{models: string[]}` | `s18.js:4904` |
| 5 | POST | `/api/user/restore-subscription` | Abonelik geri yükle (iOS/mobil) | — | — | `s18.js` |
| 6 | POST | `/api/user/create-paddle-transaction` | Paddle ödeme oturumu | — | — | `s27.js` |
| 7 | POST | `/api/user/create-phone-trial-transaction` | Telefonla trial başlat | — | — | `s27.js` |
| 8 | POST | `/api/user/mobile-trial-started` | Mobil trial işaretle | — | — | `s27.js` |
| 9 | POST | `/api/user/sync-locale-cookie` | Dil/locale cookie senkronu | — | — | `s24.js` |
| 10 | GET | `/api/user/academy` | Academy üyeliği | — | — | `s8.js` |

### 2.1 Gating kararı (istemci tarafı)

```ts
const blocked = !isPaid && (isAtDailySessionLimit || (isTrialExhausted && hardPaywallEnabled));
```

**Canlı yanıt (doğrulandı, 28.10.2026):**

```json
{
  "isPaid": false, "isSponsored": false,
  "dailyValidSessionsCount": 1, "lifetimeValidSessionsCount": 5,
  "isTrialExhausted": true, "isDailySessionCountExceeded": false,
  "isSessionDurationExceeded": false, "isPreviousDaySessionBlocked": false,
  "isAtDailySessionLimit": false, "sessionIncludedInTrial": false,
  "numNonTrialedSessionsDoneToday": 1,
  "hardPaywallEnabled": false, "softFeatureGateEnabled": true,
  "limits": { "freeTrialMinutes": 15, "dailySessionCountLimit": 2, "perSessionTimeLimitMinutes": 10 },
  "stopReason": null
}
```

→ Gerçek limitler: **günde 2 ders · ders başına 10 dk · toplam 15 dk ücretsiz**.
`isSponsored` ve `softFeatureGateEnabled` kodda yoktu; canlıda ortaya çıktı.

- Ders **başında** (home) ve ders **sırasında** ayrı ayrı kontrol edilir.
- Ders sırasında 10 saniyede bir yoklanır (heartbeat ile aynı kadans — 5 dk'da 31 çağrı).
- `lifetimeValidSessionsCount` hem `create` isteğinde hem gating yanıtında var → istemci de sunucu da sayıyor.

---

## 3. Attachment / RAG — 7 uç

| # | Method | Path | Amaç | İstek | Yanıt | Kanıt |
|---|---|---|---|---|---|---|
| 1 | POST | `/api/attachment/process` | Dosyayı işle (senkron) | `{storagePath, fileName, mimeType, thumbnailPath?, sessionId?}` | `Document{chunks, processing, extractedText, ...}` | `s9.js:770` |
| 2 | POST | `/api/attachment/process-with-status-streaming` | Dosyayı işle (canlı durum) | aynı + `studyGuideProcessing?` | NDJSON durum akışı → final `Document` | `s9.js:789` |
| 3 | POST | `/api/attachment/process-link-streaming` | URL/link içeriğini çek + işle | — | NDJSON | `s9.js:93` |
| 4 | POST | `/api/attachment/upload-link-thumbnail` | Link önizleme görseli yükle | — | — | `s9.js:211` |
| 5 | POST | `/api/attachment/update-prompt-suggestions` | Metinden prompt önerileri üret | `{extractedText}` | `{success, suggestions:{topic, exam, homework}}` | `s8.js:34` |
| 6 | POST | `/api/attachment/consolidate-images` | Çoklu görseli birleştir | — | — | `s18.js` |
| 7 | POST | `/api/attachment/proxy-download` | CORS'lu indirme proxy'si | — | dosya | `s18.js` |

### 3.1 Yükleme hattı

```
1. POST /api/session/signed-upload-urls   → {storagePath, signedUrl, thumbnail...}
2. PUT  <signedUrl>                        (doğrudan S3/R2, sunucudan geçmez)
3. POST /api/attachment/process-with-status-streaming
4. Document.chunks → oturumun RAG bağlamı
5. Tur sırasında model `document_retrieved` / `chunk_retrieved` komutlarıyla atıf yapar
```

---

## 4. Voice — 3 uç

| # | Method | Path | Amaç | İstek | Yanıt | Kanıt |
|---|---|---|---|---|---|---|
| 1 | POST | `/api/voice/get-token` | Kısa ömürlü STT token'ı | `{tokenId: "token-1"}` | `{token}` | `s40.js:182`, `:413`, `:523` |
| 2 | POST | `/api/voice/get-tts` | TTS sentezi | `{text, voice, locale, sampleRate:24000, speed, model:"model-3", prebufferMs:640, style?, styleDegree?, includeTimestamps, sessionId, userId, clientId, turnIndex, actionInternalId}` | ses chunk'ları + viseme / word-boundary event'leri | `s36.js:308`, `:646` |
| 3 | POST | `/api/voice/track-stt-session` | STT kullanım telemetrisi | `{provider:"deepgram"\|"azure"\|"assemblyai", model, audio_duration_seconds, session_duration_seconds}` | — | `s40.js:255`, `:488` |

### 4.1 Sağlayıcı seçimi

| Koşul | STT | Model |
|---|---|---|
| Dil = `en` | Deepgram | `flux-general-en` |
| Diğer diller | Azure Speech | dile göre neural model |
| Yedek / kesinti | AssemblyAI | `universal-streaming-english` / `universal-streaming-multilingual` |

Format: 16 kHz PCM, streaming. TTS: varsayılan Azure `DragonHDLatestNeural`, alternatif OpenAI; 24 kHz, `prebufferMs: 640` ile ilk ses gecikmesi kısaltılıyor.

> `get-tts` gövdesi bir webpack worker chunk'ı (modül 7488/5107) içinde kuruluyor; bu chunk indirilemediği için alanlar ana bundle'daki konfigürasyondan çıkarıldı — **canlı doğrulanmadı**.

---

## 5. Misc — 8 uç

| Method | Path | Amaç | Kanıt |
|---|---|---|---|
| POST | `/api/client-log` | İstemci telemetrisi | `s17.js`, `s18.js`, `s24.js` |
| GET | `/api/dialogs/active` | Aktif modal/dialog | `s24.js` |
| POST | `/api/dialogs/dismiss` | Dialog kapat | `s24.js` |
| POST | `/api/dialogs/complete` | Dialog tamamla | `s24.js` |
| GET | `/api/pricing/paddle-localized` | Lokalize fiyatlandırma | `s18.js` |
| GET | `/api/doc-repo/document-thumbnail/{slug}` | Doküman küçük resmi (`?path=`) | `s3.js:3827` |
| GET | `/api/academy/sessions/{id}/details` | Academy ders detayı | `s18.js:4876` |
| GET | `/api/academy/students/{id}/sessions` | Öğrenci dersleri | `s18.js:4883` |

---

## 6. Admin — 6 uç

| Method | Path | Amaç | Parametre | Kanıt |
|---|---|---|---|---|
| GET | `/api/admin/analytics/inspect-session/details` | Oturum inceleme | `?id&env` | `s18.js:4806` |
| POST | `/api/admin/analytics/inspect-session/run-analysis` | Oturum analizi çalıştır | `{sessionId}` | `s18.js:4817` |
| GET | `/api/admin/analytics/sessions-data` | Toplu veri (NDJSON) | — | `s18.js:4856` |
| GET | `/api/admin/analytics/user-sessions` | Kullanıcı araması | `?query` | `s18.js:4851` |
| GET | `/api/admin/manual-intervention/live-sessions` | Canlı oturum listesi | `?env&since` | `s18.js:4837` |
| GET | `/api/admin/manual-intervention/user-details` | Kullanıcı detayı | `?userId` | `s18.js:4846` |

`manual-intervention` uçları, ürünün canlı derse **insan müdahalesi** (destek/gözlem) özelliğini de barındırdığını gösteriyor. Admin yüzeyi aynı SPA içinde, ayrı bir build değil.

---

## 7. Session event modeli (32 tip)

```
Yaşam döngüsü : session_started, session_resumed, session_reset,
                session_ended_by_user, session_ended_by_tutor,
                session_ended_with_paywall, session_ended_with_reset
Mesaj         : message_sent_by_user, message_received_from_tutor
Tahta         : whiteboard_element_added, whiteboard_element_edited,
                whiteboard_element_removed, whiteboard_cleared,
                whiteboard_annotation_added
Bağlam        : context_collapsed, collapsed_block_expanded
RAG           : document_retrieved, chunk_retrieved
Ders          : skill_loaded, lesson_plan_updated
Ayar          : speech_speed_changed, user_speech_language_changed,
                captions_enabled, captions_disabled
Hata          : action_failed
— canlıda eklenenler (kodda yoktu) —
Oturum kontrol: session_paused(reason: inactivity|transcript_open),
                session_unpaused(reason: pause_button|transcript_close)
Transkript    : transcript_opened, transcript_closed(source: user)
```

`whiteboard_element_edited` özellikle önemli: `draw` komutu **iki fazlı** çalışıyor — önce boş `whiteboard_element_added` (`content: ""`), görsel üretilince aynı element `whiteboard_element_edited` ile Supabase URL'i alıyor.

Event zarfı (tüm tiplerde ortak alanlar):

```ts
{
  turnIndex: number,        // kaçıncı model turu
  orderIndex: number,       // tur içi sıra
  subOrderIndex: number,    // komut içi parça
  timestamp: number,
  name: EventName,
  elementId?: string,
  position?: { spaceId: string, columnId: number },
  contentType?: string,
  content?: string,
  text?: string,
  speechStyle?: string,
  actionItem?: object,
  collapseId?: string
}
```

Beyaz tahta, event log'un türevi: `(spaceId, columnId)` konumlu elementler + `whiteboard_*` event'leri. Yani tahta ayrı bir state değil, **event sourcing** ile yeniden inşa ediliyor. Bu yüzden `PATCH /api/session/{id}` ile tek bir delta hem sohbeti hem çizimi senkronluyor.

---

## 8. Uçtan uca akış (özet)

```
HOME
 └─ POST /api/user/check-session-gating
 └─ POST /api/session/signed-upload-urls → PUT S3 → POST /api/attachment/process-with-status-streaming
 └─ POST /api/attachment/update-prompt-suggestions
 └─ POST /api/session/create → {sessionId}

TUTOR SESSION  /tutor-session/{id}
 └─ GET  /api/session/{id}
 └─ GET  /api/session/{id}/events?from=0&limit=1000
 └─ GET  /api/user/settings

TUR DÖNGÜSÜ
 ├─ kullanıcı: metin veya ses
 │   └─ ses: POST /api/voice/get-token → WS stream → POST /api/voice/track-stt-session
 ├─ POST /api/session/generate-response   (NDJSON komut akışı)
 │   ├─ _stream:"svg" → kademeli çizim
 │   ├─ speak → POST /api/voice/get-tts → ses kuyruğu
 │   └─ write_/draw/erase/... → tahta
 ├─ PATCH /api/session/{id}   {appended, removed, head, persistedCount}
 ├─ POST /api/session/{id}/heartbeat      (10 sn)
 └─ POST /api/user/check-session-gating   (10 sn)

BAKIM
 ├─ POST /api/session/collapse-context    (son 24 turdan sonra)
 └─ POST /api/session/fix-latex           (render hatasında)

BİTİŞ
 ├─ POST /api/session/{id}/finish {reason, timeSpent}
 └─ POST /api/session/feedback
```

---

## 9. Doğrulanmamış / riskli noktalar

| Konu | Durum |
|---|---|
| Auth mekanizması | `Authorization` header'ı **yok**, cookie tabanlı olduğu doğrulandı; cookie **adı** hâlâ belirsiz (CDP cookie değerlerini göstermiyor). |
| `get-tts` gövdesi | TTS bir **worker target**'ta çalışıyor; ilk kayıtta worker'a attach edilmedi. v2 kaydedici bunu düzeltiyor. |
| `generate-response` NDJSON | ✅ **doğrulandı** — 88 çerçeve, [`live-protocol.md`](live-protocol.md) |
| `x-tutor-ladder-source` | 7 yanıtın hiçbirinde gözlenmedi → koşullu header, varlığı doğrulanamadı. |
| Rate limit / kota değerleri | ✅ gating yanıtında: 2 ders/gün, 10 dk/ders, 15 dk deneme |
| Admin uçlarının yetkilendirmesi | İstemci tarafında rol kontrolü görülmedi → sunucu tarafında olmalı. |
| `collapse-context` eşiği | Tur sayısı eşiğe ulaşmadı; kodda "son 24 model turu" sabiti var. |
| STT akışı | Oturumda mikrofon kullanılmadı (`micEnabledByDefault:false`). |

Bu noktaları kapatmanın en kısa yolu, kullanıcının kendi oturumunda pasif bir ağ dinleyicisi (REA `observe-web-session`) çalıştırıp gerçek istek/yanıt gövdelerini toplamaktır.
