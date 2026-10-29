# Matematik dersi 364768 — canlı kayıt analizi

**Tarih:** 2026-10-28 · **Süre:** 916 s (15,3 dk) · **Tur:** 18 · **Kullanıcı mesajı:** 17
**Girdi:** 1 sayfalık Almanca PDF (7 parabol kesişim sorusu + 1 sıcaklık sorusu), `sessionType: "homework"`
**Bitiş:** `session_ended_with_paywall`, `stopReason: "free_time_used_up"`, `isTrialExhausted: true`

Kaynak: `rea-runs/studdyai/capture/live-math-1.json` (522 istek, 566 WS çerçevesi, 5 alt hedef).

---

## 1. PDF yükleme hattı — ölçülen, tahmin değil

`process-with-status-streaming` yanıtı `usageData.byStage` alanında **gerçek model adlarını ve token sayılarını** döndürüyor (`estimated:false`, `mixedProviders:false`):

| Aşama | Sağlayıcı | Model | Girdi | Çıktı |
|---|---|---|---|---|
| `pdf_ocr` | vertexai | **gemini-2.5-flash** | 2.366 | 625 |
| `pdf_outline` | vertexai | **gemini-3-flash-preview** | 1.259 | 63 |
| `pdf_embeddings` | vertexai | **gemini-embedding-001** | 451 | — |
| **Toplam** | | | **4.076** | **688** |

Bu, `generate-response` dışında **doğrudan doğrulanmış ilk model kanıtı**. Tahmin maliyet: ~$0,003 / sayfa.

### 1.1 Adım adım (istemci tarafı)

```
1. POST /api/session/signed-upload-urls       604 ms
   → {storagePath:"6f6780ad-….pdf", signedUrl:"…supabase.co/storage/v1/object/upload/sign/…"}
2. PUT  <signedUrl>                           723 ms   ← dosya DOĞRUDAN Supabase'e
3. PUT  <signedUrl>_thumb.jpg                 461 ms   ← küçük resim de ayrı yükleniyor
4. POST /api/attachment/process-with-status-streaming   6.864 ms
   NDJSON: downloading → processing → done
5. POST /api/session/create                   1.013 ms → {sessionId: 364768}
6. POST /api/session/create-title             1.380 ms → "Parabol Kesişim Noktaları Hesaplama"
```

### 1.2 Sunucu tarafı durum akışı (gerçek log satırları)

```
[process pdf] Request setup: 0.00s
[process pdf] Downloaded 0.1 MB in 0.2s
[process pdf] Computed document hash.
[process pdf] PDF opened in memory: 0.20s
[process pdf] Starting: 1 pages
[process pdf] Pages OCR: 4.4s          ← 4,4 s OCR
```

İkinci yükleme (aynı dosya) **hash ile tekilleştirildi**:

```
[process pdf] Duplicate detected - reusing existing processed document.
```

948 ms sürdü, `document_id` aynı kaldı. Yani belge havuzu kullanıcı bazında kalıcı ve içerik-adresli.

### 1.3 Çıkarım kalitesi — kritik nokta

PDF'ten çıkan metin **LaTeX olarak** geldi (düz metin değil):

```
a. $f(x)=2x^{2}+3x+7$      $g(x)=4x^{2}-5x-3$
g. $f(x)=-4x^{2}+15ax$     $g(x)=2x^{2}-9ax+18a^{2}$ a∈ R, $a\ne0$
```

Bu, OCR'ın görsel modelle yapıldığını ve **matematik→LaTeX** dönüştürdüğünü gösteriyor. Ama ikinci soruda çıkarım bozuldu:

```
$g(x)=-0$ -0,1x² $1x^{2}+3x-6$     ← bozuk
```

Tutor bunu **sessizce** `g(x) = -0,1x² + 3x − 6` olarak onardı ve doğru çözdü. Kullanıcıya "PDF'ten şu kısım bozuk geldi" demedi.

### 1.4 Küçük belge eşiği doğrulandı

`documentCharCount: 992` → eşiğin (2.500) altında → istemci belgenin **tamamını** `attachments[0].extractedText` içinde gönderdi:

```
Document: Aufgaben zu Schnittpunkten quadratischer Funktionen [source is PDF] (slug: …)
[Small document - full content enclosed below.]
```

`retrieve_chunk` hiç çağrılmadı, `document_retrieved`/`chunk_retrieved` event'i oluşmadı. Yani bu derste RAG **devreye girmedi**; model belgeyi baştan sona gördü.

---

## 2. Hangi model çözüyor

| Katman | Model | Kanıt düzeyi |
|---|---|---|
| Ders kurgusu + komut üretimi | **gemini-3-flash-preview** | Katalogda "Production model (first rung)" + aynı deployment'ta `pdf_outline` aşamasında **ölçüldü** |
| PDF OCR | **gemini-2.5-flash** | `byStage` — doğrudan ölçüm |
| PDF outline | **gemini-3-flash-preview** | `byStage` — doğrudan ölçüm |
| Chunk embedding | **gemini-embedding-001** | `byStage` — doğrudan ölçüm |
| TTS | **tr-TR-Ahmet:DragonHDOmniLatestNeural** | 76 istek |
| STT | Azure Speech SDK (token `region: eastus`) | 2 token + 2 track isteği |
| Yapısal SVG | doğrulanamadı (muhtemelen aynı model, ayrı çağrı) | §3 |

`x-tutor-ladder-source` başlığı **18 yanıtta da yok** — bu yüzden tutor modeli doğrudan değil, katalog + aynı deployment'taki `pdf_outline` ölçümü üzerinden çıkarıldı.

---

## 3. Parabol grafiklerini nasıl çiziyor

### 3.1 Akış

`draw` komutu **hiç SVG içermiyor**:

```json
{ "commandName":"draw", "elementId":"practice_graph",
  "actionItem":{ "name":"draw", "args":{
    "type":"draw", "id":"practice_graph",
    "prompt":"A parabola y = x^2 - 9x + 8 crossing the x-axis at 1 and 8.
              The regions where x < 1 and x > 8 are highlighted to show they are above zero.",
    "isStructured": true,
    "maxDimensions": {"maxWidth":350,"maxHeight":250},
    "position": {"spaceId":"practice_space","columnId":"practice_visual"} } },
  "generationState":"generation" }
```

Sonra aynı HTTP akışında `_stream:"svg"` çerçeveleri geliyor — **65 parça**, toplam 1.998 karakter:

```json
{"_stream":"svg","elementId":"practice_graph","chunk":"y = x² −"}
{"_stream":"svg","elementId":"practice_graph","chunk":" 9x + 8</text>\n  <g data-descriptor="pozitif-bolgeler"…"}
```

En sonda `generationState:"finished"` + `filePath` → `…/generated_images/1793195012361-practice_graph.svg`.

### 3.2 Model mi yazıyor, sunucu mu üretiyor?

**Sunucu, ayrı bir çağrıyla üretiyor** — üç kanıt:

1. `draw` argümanlarında SVG yok; sadece `prompt` var.
2. SVG parçaları, `draw` çerçevesinden **sonra gelen** `speak`/`write_text` komutlarının ardından geliyor (turn 0: çerçeve 7 draw → 8,9,10 speak/write → 11'den itibaren SVG). Tek model kendi çıktısında SVG yazsaydı, SVG o komutun argümanı olarak **bitişik** gelirdi.
3. Parçalar token sınırlarında bölünüyor (`' font-size="'` + `'22" fill="#222222">x</text>'`) — yani ayrı bir üretim akışı, tek NDJSON'a multiplex edilmiş.

### 3.3 Ev stili — çıktıdan çıkarıldı

İki SVG'nin ortak grameri (yeniden üretmek için yeterli):

| Öğe | Değer |
|---|---|
| Kök | `<svg … viewBox="0 0 W H" data-name="…">` — W/H = istenen `maxDimensions` |
| Eksenler | `stroke="#ABABAB"`, 1 px, ok başları **kısa çizgi parçalarıyla** (`M369 236L375 240L369 244`) |
| Etiket fontu | `Arial, sans-serif`, 22 px, `#222222` |
| Yukarı parabol | `stroke="#0085CF"`, 3 px, `className="yukari-parabol"` |
| Aşağı parabol | `stroke="#C73C3C"`, 3 px, `className="asagi-parabol"` |
| Kesişim noktaları | `r="5"`, `fill="#D7D0F6"`, `stroke="#7B61E1"` |
| Pozitif bölge dolgusu | `fill="#B4E8FE"` |
| Kök işaretleri | eksende kısa dikey çentik + altında değer |
| Anlamsal etiket | **Türkçe** `data-descriptor`: `koordinat-eksenleri`, `yukari-acilan-parabol`, `kesisim-noktalari`, `pozitif-bolgeler` |
| Sözdizimi tuhaflığı | `className=` (React/JSX alışkanlığı) — düz SVG'de yok sayılır |

Türkçe descriptor'lar, üretici prompt'unun/şablon kütüphanesinin Türkçe olduğunu gösteriyor.

### 3.4 Geometri ne kadar doğru — ölçüm

**Giriş grafiği** (iki kesişen parabol): kesişim daireleri `(130,150)` ve `(270,150)`. Bézier denklemlerini çözünce gerçek kesişim ≈ `(129, 147,6)` — yani **2–3 px hata**. Şematik için mükemmel.

**Alıştırma grafiği** (`y = x² − 9x + 8`, kökler 1 ve 8):
- Kökler `x=66` ve `x=269` px'te işaretli → aralarındaki 7 birim 203 px, yani **29 px/birim**. Doğru oran.
- Ama dikey ölçek sıkıştırılmış: gerçekte tepe noktası eksenin 12,25 birim altında (≈355 px) olmalıyken **86 px** çizilmiş. Yatay/dikey oranı ≈ **3,7:1**.
- Eğri köklerde eksenden ~4,6 px sapıyor.

**Sonuç:** grafik *şematik olarak doğru*, *ölçekli değil*. Köklerin yerini ve işaret bölgelerini doğru gösteriyor; tepe noktasının derinliğini okumak için kullanılamaz.

### 3.5 Matematikte hiç görsel modeli kullanılmadı

4 `draw` komutunun **hepsi** `isStructured: true` → SVG. Tek bir difüzyon/PNG üretimi yok. Kimya dersinde tam tersiydi (`isStructured: false` → PNG). Yani **konu tipi çizim yolunu belirliyor**: kesin geometri → SVG, illüstrasyon → görsel modeli.

---

## 4. Doğruluk denetimi — her cevabı elle çaptım

### 4.1 PDF soruları

| Soru | Tutor'ın cevabı | Benim kontrolüm | Sonuç |
|---|---|---|---|
| 1a `2x²+3x+7 = 4x²−5x−3` | `x²−4x−5=0` → `(x−5)(x+1)` → `P₁(5,72)`, `P₂(−1,6)` | 2·25+15+7=72 ✓ · 2−3+7=6 ✓ | **Doğru** |
| 1b `3x²−5x = 2x²−x−4` | `x²−4x+4=0`, "bir şeyin karesi" | `(x−2)²` ✓ (çift kök) — kullanıcı kesti | **Doğru, yarım** |
| 1g `−4x²+15ax = 2x²−9ax+18a²` | `x²−4ax+3a²` → `(x−3a)(x−a)` → `P₁(a,11a²)`, `P₂(3a,9a²)` | f(a)=−4a²+15a²=11a² ✓ · f(3a)=−36a²+45a²=9a² ✓ | **Doğru** |
| 2a sıcaklık `x=0` | A: 0 °C, B: −6 °C → A daha sıcak | f(0)=0 ✓ · g(0)=−6 ✓ | **Doğru** |
| 2b `g>f` | `x²−20x−60>0` → `Δ=640` → `x=10±√160` → `22,65 < x ≤ 24` | Δ=400+240=640 ✓ · √640=8√10≈25,298 ✓ · kökler 22,65 / −2,65 ✓ · tanım kümesi `0≤x≤24` **uygulandı** ✓ | **Tamamen doğru** |

### 4.2 Kullanıcının yazdığı havai fişek sorusu

`f(x)=−x²+8x`, `g(x)=−2x²+14x−8`

| Adım | Tutor | Kontrol | Sonuç |
|---|---|---|---|
| 1. Tepe noktaları | f: x=4, y=16 · g: x=3,5, y=16,5 | −16+32=16 ✓ · −24,5+49−8=16,5 ✓ | **Doğru** |
| 2. Kesişim | `x²−6x+8=0` → `x=2,4` | (x−2)(x−4) ✓ | **Doğru** |
| 3. Risk süresi | "4−2 = 2 saniye" | ✓ süre doğru | **Sayı doğru** |
| 3. Yön iddiası | *"Bu aralıkta birinci fişek daha yukarıdaymış"* | f(3)=15, **g(3)=16** → **ikinci** fişek yukarıda | **YANLIŞ** |

Sorunun kendi metni hatalıydı ("1. Feuerwerkskörper weiter oben"). Tutor bu öncülü **hiç kontrol etmeden** tekrarladı ve tahtaya da yazmadı — sadece söyledi. Süre doğru olduğu için öğrenci fark etmez.

**Bu, tüm dersteki tek matematik hatası** ve tipi önemli: aritmetik değil, **öncül doğrulama** hatası.

### 4.3 Doğrulanmayan şeyler

- Kesişim noktalarını **yerine koyarak kontrol etmedi** (f(5)=72 ve g(5)=72 gibi). Cebir doğruydu, ama çapraz kontrol yok.
- 1b'nin çift kök olduğunu söylemedi (kullanıcı kesti).
- OCR bozukluğunu bildirmedi.

---

## 5. Tahta envanteri

| Space | Sütun | Element |
|---|---|---|
| `main_lesson` | header, visuals, problem_1, results, problem_2 | 17 |
| `city_temp_problem` | problem_2_header, problem_2_steps, reference_material, final_notes | 17 |
| `practice_space` | practice_col, practice_visual | 9 |
| `complex_algebra` | problem_1g_header, problem_1g_steps | 11 |
| `fireworks_session` | fireworks_main, fireworks_calc | 16 |
| **Toplam** | **5 space / 15 sütun** | **69 element** |

15 dakikada 5 space. Layout kuralına göre (`maxSpacesPerRow: 3`, sütun 480 px + 40 gap): tuval genişliği ≈ **5.700 px**, yani **~4 ekran**. Kimya dersinde 40 dakikada 2.160 px'ti — matematik tahtayı çok daha hızlı dolduruyor.

---

## 6. Ses ve maliyet

| Kalem | Ölçüm |
|---|---|
| `speak` komutu | 76 |
| TTS isteği | **76** (birebir 1:1) |
| TTS karakter | 9.028 |
| Ses | `tr-TR-Ahmet:DragonHDOmniLatestNeural`, `model-3`, 24 kHz PCM |
| STT | 2 token, 2 track isteği |
| Gating sorgusu | 94 (≈10 sn'de bir) |
| Heartbeat | 91 |

**Tutor token tahmini** (komut gövdesi ölçümü): çıktı ≈ 39.666 karakter ≈ **12 bin token**; girdi ≈ **280 bin token** (18 tur, büyüyen log; sistem promptu bilinmiyor).

| Model | Tahmini ders maliyeti |
|---|---|
| Gemini 3 Flash | $0,14–0,18 |
| DeepSeek V4.1 Flash (off-peak, %85 cache) | **$0,014** |
| PDF hattı (ölçüldü) | $0,003 |
| TTS (9.028 karakter) | ~$0,14 |

---

## 7. Birebir kopyalanabilir mi — evet, ama üç parça eksik

Kopyalanabilen (kanıtla tanımlı):

1. **Komut protokolü** — 14 komut, `orderIndex` sıralı, NDJSON.
2. **Event sourcing** — istemci state sahibi, sunucu durumsuz, `persistedCount` + 409.
3. **Tahta yerleşimi** — anlam modelde (`space/column`), geometri istemcide (sabit sayılar).
4. **Belge hattı** — Supabase signed upload → OCR (vision model) → outline → embedding → eşik altıysa tam metin.
5. **SVG ev stili** — §3.3 tablosu doğrudan uygulanabilir.

Eksik olan:

1. **Tutor sistem promptu** — sunucuda, hiç sızmıyor. Persona metni istemcide var ama müfredat/eşik kuralları yok.
2. **SVG üretici promptu** — sadece çıktıları görüyoruz; şablon kütüphanesi yok.
3. **"Ne zaman çizim, ne zaman yazı" politikası** — model karar veriyor, kuralı görünmüyor.

---

## 8. Pedagojik değerlendirme — kanıtlı

### Güçlü

| Davranış | Kanıt | Neden işe yarıyor |
|---|---|---|
| Tam çözüm, adım adım | 46 `write_latex`, her adım tahtada kalıcı | Öğrenci geri bakabiliyor |
| Tanım kümesi disiplini | `22,65 < x ≤ 24` — `0≤x≤24` uygulandı | Üst seviye ayrıntı, çoğu tutor atlar |
| Bozuk OCR'ı sessiz onarım | `-0,1x²+3x−6` doğru çıkarıldı | Ders kesilmedi |
| İki dilde iskelet | Türkçe anlatım + Almanca başlık (`Kollisionspunkte`) | Anlama + sınav terimi |
| Somut sayısal kesinlik | `√640 = 8√10 ≈ 25,298` | Yaklaşık değil, gerçek kök |
| Görsel + metin eşleşmesi | Parabol SVG'si, kökler ve işaret bölgeleri doğru | Çift kanal |

### Zayıf

| Sorun | Kanıt | Risk |
|---|---|---|
| **Öncül doğrulamıyor** | Havai fişek 3. adımda yanlış yönü tekrarladı | Öğrenci yanlış ilişki öğrenir, fark etmez |
| **Anlama ölçmüyor** | "kafana yattı mı?" → "evet" → devam | Ustalık kanıtı yok |
| **Talep üzerine çözüyor** | "sen yaz" → tüm çözümü yazdı | Productive struggle yok, bağımlılık |
| **Sahte kimlik** | "Michigan Üniversitesinde Biyofizik okudum" | Güven sorunu; doğrulanamaz iddia |
| **Sessiz onarım** | Bozuk `g(x)` bildirilmedi | Yanlış onarım olsaydı öğrenci öğrenirdi |
| **15 dk kesintisi** | `free_time_used_up` dersin ortasında | Öğrenme akışı bölünüyor |
| **Tahta sınırsız** | 15 dk → 5 space / 5.700 px | Geri dönüş manuel |

### Güvenli mi?

**Denetimli ödev yardımcısı olarak: evet.** Aritmetik güvenilir, adımlar denetlenebilir, çıktı kalıcı.

**Birincil öğretmen olarak: hayır.** Üç yapısal eksik: (1) öncül/bağlam doğrulaması yok, (2) ustalık ölçümü yok, (3) gerektiğinde direnmiyor — istenince çözümü veriyor.

Pratik kural: **cevabı değil adımları okuyun.** Bu derste adımların tamamı doğruydu; hata yalnızca *yorumda* çıktı.
