# Matematik Ajanı — üretim raporu

Bu rapor, Studdy AI'nin matematik dersini **yeniden üretmek** için gereken her şeyi içerir.
Analiz dosyası: `matematik-364768.md` · Ham kayıt: `rea-runs/studdyai/capture/live-math-1.json`

Kararlar bu belgede; gerekçeler kanıt numaralarıyla.

---

## 0. Tek sayfalık özet

| Soru | Cevap |
|---|---|
| Sayfa ne yapıyor | PDF/denklem alır → tahtaya adım adım çözer → sesli anlatır |
| Kaç model | 4 (OCR, outline, tutor, SVG) + ses |
| Tutor modeli | `gemini-3-flash-preview`, `thinkingLevel:"MINIMAL"` |
| Maliyet | 15 dk ders ≈ $0,15 metin + $0,14 ses + $0,003 PDF |
| Doğruluk | Aritmetik güvenilir; **öncül doğrulama yok** |
| Kopyalanabilir mi | Evet — sistem promptu ve SVG şablonları hariç |
| Bizim farkımız | Matematiği **deterministik motorda** çöz, modeli sadece anlatıcı yap |

---

## 1. Doğrulanmış ölçümler

Bu sayılar tahmin değil; `usageData.byStage` alanından (`estimated:false`).

### 1.1 PDF hattı (1 sayfa, 0,1 MB)

| Aşama | Model | Girdi | Çıktı | Süre |
|---|---|---|---|---|
| `pdf_ocr` | `gemini-2.5-flash` | 2.366 | 625 | 4,4 s |
| `pdf_outline` | `gemini-3-flash-preview` | 1.259 | 63 | — |
| `pdf_embeddings` | `gemini-embedding-001` | 451 | — | — |
| **Toplam** | | **4.076** | **688** | 6,9 s |

### 1.2 Ders (15,3 dk, 18 tur)

| Ölçüm | Değer |
|---|---|
| `speak` komutu | 76 |
| TTS isteği | 76 (birebir 1:1) |
| TTS karakter | 9.028 |
| `write_latex` | 46 |
| `write_text` | 21 |
| `draw` | 4 (hepsi `isStructured:true` → SVG) |
| Tahta | 5 space / 15 sütun / 69 element |
| Gating sorgusu | 94 (10 sn'de bir) |
| Çıktı token (tahmin) | ~12 bin |
| Girdi token (tahmin) | ~280 bin |

### 1.3 Belge eşiği

```
< 2.500 karakter  → [Small document - full content enclosed below.]  → tam metin prompt'a
> 2.500 karakter  → outline ağacı + retrieve_chunk(doc_slug, index_path)
```

Eşik altındaysa RAG **hiç devreye girmiyor**; `document_retrieved`/`chunk_retrieved` event'i oluşmuyor.

---

## 2. Sayfa yapısı (birebir hedef)

```
┌──────────────────────────────────────────────┬──────────────────┐
│  TAHTA (figboard)                            │  KONUŞMA         │
│  ┌────────────────────────────────────────┐  │  ┌────────────┐  │
│  │  space: main_lesson                    │  │  │ tutor msgs │  │
│  │  ┌──────────┬──────────┬────────────┐  │  │  │ (scroll)   │  │
│  │  │ problem  │ results  │  visuals   │  │  │  └────────────┘  │
│  │  └──────────┴──────────┴────────────┘  │  │  ┌────────────┐  │
│  │  space: city_temp_problem              │  │  │ composer   │  │
│  └────────────────────────────────────────┘  │  └────────────┘  │
│  [zoom %] [Folgen ●]                         │                  │
└──────────────────────────────────────────────┴──────────────────┘
```

**Etkileşimler**

| Öğe | Davranış |
|---|---|
| Tahta sürükleme | Pan |
| Tekerlek | Zoom (0,3–2,0), imlecin altındaki nokta sabit kalır |
| "Folgen" | Kamerayı en son değişen elemente uçurur |
| "⌖" | Tüm tahtayı ekrana sığdırır |
| Yeni element | Kamera otomatik takip eder (yalnızca "Folgen" açıksa) |
| Composer | Enter → gönder; Shift+Enter → satır |

Uygulamadaki space adları Almanca: `aufgabe` / `loesung` / `grafik`
(Studdy'nin `main_lesson` / `city_temp_problem` adları ders başına değişiyor,
sabit bir anlam taşımıyor). Sütunlar: `eingabe` / `schritte` / `ergebnis` /
`schaubild`.

---

## 3. Komut protokolü

Studdy'nin 14 komutundan bu sayfa için 7'si yeterli:

```json
{"orderIndex":0,
 "commandName":"write_latex",
 "elementId":"step_1",
 "position":{"spaceId":"loesung","columnId":"schritte"},
 "actionItem":{"name":"write_latex","args":{
   "type":"write_latex","id":"step_1",
   "latex":"2x^{2}+3x+7 = 4x^{2}-5x-3",
   "position":{"spaceId":"loesung","columnId":"schritte"}}}}
```

| Komut | Argüman | Ürettiği event |
|---|---|---|
| `write_text` | `text` (HTML) | `whiteboard_element_added` `contentType:"html"` |
| `write_latex` | `latex` | `… contentType:"latex"` |
| `draw` | `svg` (tam SVG) veya `template`+`params` | `… contentType:"svg-drawing-inline"` |
| `highlight` | `targetElementId` | `whiteboard_annotation_added` |
| `circle_annotate` | `targetElementId` | `… annotationType:"circle"` |
| `speak` | `text` | `message_received_from_tutor` |
| `finish` | — | `session_ended_by_tutor` |

**Kural:** aynı `elementId` ikinci kez gelirse `added` değil `edited` üretilir
(log geriye taranır; `whiteboard_cleared` veya aynı id'nin `removed`'ı görülürse sıfırlanır).

---

## 4. Tahta yerleşimi — sabitler

Anlam modelde, geometri istemcide. Model sadece `{spaceId, columnId}` seçer; hiçbir zaman x/y görmez.

```js
{ columnWidth: 400, elementGap: 24, columnGap: 40,
  spaceGap: 80, rowGap: 160, maxSpacesPerRow: 3 }
```

| Kural | Uygulama |
|---|---|
| Sütun içi | Dikey yığın; yükseklik **ölçülür**, aralarına 24 px |
| Sütunlar | Soldan sağa, 40 px; genişlik `max(400, en geniş element)` |
| Element | `max-width: 520 px` (SVG 520 × 380) |
| Space'ler | Soldan sağa, 80 px; **3 space sonra satır atla** (+160 px) |
| Sıra | Elementin **ilk göründüğü an** (`turn → order → subOrder`) |
| Düzenleme | Sırayı bozmaz; yalnızca space/column değişimi yeniden damgalar |

---

## 5. SVG ev stili

Studdy'nin çıktısından çıkarıldı; şablonlarımız bu grameri izler.

| Öğe | Değer |
|---|---|
| Eksen | `#ABABAB`, 1,5 px |
| Etiket | Arial 12 px (sayılar) / 16 px (x, y), `#222222` |
| Gitter | `#E2E8F0`, 1 px |
| Yukarı parabol | `#0085CF`, 3 px |
| Aşağı parabol | `#C73C3C`, 3 px |
| Kesişim | `r=5`, `fill #D7D0F6`, `stroke #7B61E1` |
| Pozitif bölge | `fill #B4E8FE`, opaklık 0,38 |
| Işın/projeksiyon | kesikli, `#94A3B8` |

**Farkımız (ölçülmüş, §5'teki ilk tahmin düzeltildi):** Studdy'nin grafiği şematik —
dikey ölçek 3,7:1 sıkıştırılmış (tepe 86 px'te, 355 px yerine), y ekseni **hiç
etiketlenmemiş**. Eşit birim ölçeği denendi ve **bırakıldı**: `(−1|6)` ile `(5|72)`
aynı resimde eşit ölçekle gösterilemiyor (72 birim, 6 birimin 12 katı).

Bizim kuralımız: **iki eksen bağımsız ölçeklenir, ikisi de sayıyla etiketlenir.**
Böylece her nokta okunabilir kalır ve eksenler arasında sessiz bir yalan oluşmaz —
eşit ölçek iddiası yalnızca ikisi de etiketsizken "doğru" görünür.
Çizim penceresi işaretli noktalardan (kesişim, kökler) ve tepe noktasından türetilir.

---

## 6. Doğruluk politikası — en önemli karar

Derste tek hata **aritmetik değil, öncül doğrulama** hatasıydı:
havai fişek sorusunda "1. fişek daha yukarıda" iddiası yanlıştı (f(3)=15, g(3)=16), tutor kontrol etmeden tekrarladı.

Bu yüzden mimari ters çevrilir:

```
KULLANICI ──► DENKLEM AYRIŞTIRICI ──► DETERMİNİSTİK ÇÖZÜCÜ ──► KOMUTLAR
              (parser)                 (Fraction/kesin)         │
                                                                 ▼
                                              LLM (opsiyonel) ──► SADECE ANLATIM
```

| Katman | Sorumluluk | Model? |
|---|---|---|
| Ayrıştırma | `f(x)=…`, `…=…` → polinom katsayıları | Hayır |
| Çözüm | Diskriminant, kökler, tepe, kesişim | **Hayır** — `Fraction` ile kesin |
| Görsel | Şablon SVG, iki eksen bağımsız + etiketli | Hayır |
| Anlatım | "neden böyle", sezgisel açıklama | Evet (opsiyonel) |

LLM **hiçbir sayı üretmez**; sadece hazır sayıları cümleye çevirir. LLM yoksa şablon cümleler devreye girer — ders yine çalışır.

---

## 7. Guardrail'ler (derste görülen hatalardan)

| Gözlenen sorun | Bizim kural |
|---|---|
| Öncül doğrulanmadı | Kesişimde iki fonksiyona da yerine koy (`verified`); ayrıca "hangi eğri üstte" iddiası diferansın orta noktadaki işaretinden gelir (`_which_is_above`) — tahmin yok |
| OCR bozukluğu sessizce onarıldı | Ayrıştırma başarısızsa **açıkça söyle**, tahmin etme |
| "kafana yattı mı?" → "evet" | Her çözümden sonra tek somut kontrol sorusu |
| "sen yaz" → tüm çözüm | İstek açıkça "çöz" değilse adımı öğrenciye bırak |
| Sahte kimlik ("Michigan'da biyofizik") | Persona kimlik iddiası taşımaz |
| 15 dk sonra kesinti | Sınır yok; oturum kalıcı |

---

## 8. Maliyet modeli

| Kurulum | 15 dk ders |
|---|---|
| Gemini 3 Flash + Azure TTS + SVG | **$0,29** |
| DeepSeek V4.1 Flash (off-peak, %85 cache) + Azure TTS | $0,15 |
| **Deterministik çözücü + yerel TTS** | **$0,00** |

Bizim tasarımda sayı üretimi modelde olmadığı için **en pahalı kısım (uzun çıktı) ortadan kalkıyor**:
sadece kısa anlatım cümleleri kalıyor (~1–2 bin token).

---

## 9. Kabul testleri

| # | Test | Beklenen | Ölçülen (28.10, `math_agent` + tarayıcı) |
|---|---|---|---|
| 1 | `2x^2+3x+7 = 4x^2-5x-3` | `(5\|72)`, `(-1\|6)` | ✅ `x₁=-1, x₂=5`; `P₁(-1\|6)`, `P₂(5\|72)`; kontrol cümlesi "in g eingesetzt kommt dasselbe y" |
| 2 | `-4x^2+15ax = 2x^2-9ax+18a^2` | `(a\|11a²)`, `(3a\|9a²)` — parametrik | ✅ `D=4a²=(2a)²`, `(x−a)(x−3a)=0`, `P₁(a\|11a²)`, `P₂(3a\|9a²)` |
| 3 | `-0.1x^2+3x-6 > -0.2x^2+5x`, `0≤x≤24` | `22,65 < x ≤ 24` | ✅ `x₁≈-2,65`, `x₂≈22,65` → `22,65 < x ≤ 24` (alanla kesişim) |
| 4 | `-x^2+8x = -2x^2+14x-8` | kökler 2, 4; **hangi fonksiyon üstte** doğru | ✅ kökler 2, 4; "Zwischen x = 2 und x = 4 liegt **g** oberhalb von f" — f(3)=15, g(3)=16. Dersteki yanlış cümle artık diferansın işaretinden hesaplanıyor |
| 5 | `x^2-9x+8=0` | kökler 1, 8; tepe `S(4,5\|-12,25)`; grafikte **iki eksen de sayıyla etiketli** | ✅ kökler 1, 8; `S(4,5\|-12,25)`; x ve y ekseninde sayı etiketleri var |
| 6 | Bozuk ifade `2x^^2 +` | "ayrıştıramadım" — uydurma yok | ✅ `meta.ok=false` → sarı uyarı balonu, "NICHT GERECHNET" etiketi, tahta değişmiyor |

Ek olarak tarayıcıda doğrulananlar (`check-math-page.mjs`, 1400×900 + 414×860):

| Kontrol | Sonuç |
|---|---|
| Sayfa / navbar | HTTP 200, "Mathematik-Agent" aktif, mobilde "Mathe" |
| KaTeX | 0 `katex-error`, 9–10 formül hatasız |
| SVG | 520×380, 2 eğri, 2 kesişim, 15 metin (eksen etiketleri dahil) |
| Zoom / "Folgen" / "⌖" | 100 → 115 → tüm tahta %78, hiçbir element pencere dışında değil |
| Yeni soru | tahta temizleniyor (önceki dersin sayıları yeni çözümün yanında kalmıyor) |
| Konsol hatası | 0 |

---

## 10. Uygulama dosyaları

| Dosya | İçerik |
|---|---|
| `app/math_agent.py` | Ayrıştırıcı, çözücü (kesin `Fraction`), SVG üretici, komut üretici |
| `app/templates/mathematik.html` | Tahta (pan/zoom/fit/folgen) + konuşma paneli + NDJSON istemcisi |
| `app/main.py` | `/student/mathematik`, `/api/math/agent`, `/api/math/explain`, `_math_fallback` |
| `app/templates/base.html` | Navbar: **Mathematik-Agent** (≥640 px) / **Mathe** (telefon) |
| `app/static/katex/` | KaTeX 0.16.11 yerel kopya (CDN yok): `katex.min.js`, `katex.min.css`, `fonts/*.woff2`, `LICENSE` |
| `studdyai-re/check-math-page.mjs` | CDP duman testi (giriş → sayfa → soru → zoom → ⌖ → ikinci/üçüncü soru → bozuk ifade → mobil) |

---

## 11. Teslim edilen sayfa — Studdy'den bilinçli sapmalar

Sayfa `http://127.0.0.1:8000/student/mathematik` adresinde çalışıyor; navbar'da
**Mathematik-Agent** olarak duruyor, "Fächer" ekranındaki Mathematik kartı da açık.

| Konu | Studdy | Bizim sayfa | Neden |
|---|---|---|---|
| Sayı üretimi | LLM | deterministik çözücü (`Fraction`) | dersteki tek hata öncül doğrulamaydı; sayı artık modelde değil |
| Seslendirme | Azure neural TTS (`tr-TR-Ahmet`) | tarayıcının `speechSynthesis`'i, `de-DE`, **varsayılan kapalı** ("Ton") | sıfır maliyet, sıfır anahtar; ses kalitesi Studdy'nin altında |
| Grafik | şematik, y ekseni etiketsiz | iki eksen bağımsız **ve** etiketli | 72 birimlik fark eşit ölçekte gösterilemiyor |
| Yeni soru | tahta birikir | tahta temizlenir, transkript kalır | iki dersin sayıları yan yana gelince yanlış okunuyordu |
| Ayrıştırma hatası | sessiz onarım | sarı uyarı + "NICHT GERECHNET", tahta dokunulmaz | uydurma cevap yok |
| Anlatım | tutor modeli | model varsa model, yoksa şablon cümle (`_math_fallback`) | OpenRouter şu an 403 döndüğü için pratikte hep şablon |
| Konu kapsamı | PDF + her konu | doğrusal/kuadratik, parametre `a`, tek değişkenli eşitsizlik | kapsam dışı girdi açıkça reddediliyor |
| PDF/OCR, STT | var | yok | bu sayfanın kapsamı değil |

Bilinen açıklar: canlı model anlatımı doğrulanamadı (OpenRouter 403); `x-tutor-ladder-source`
başlığı hiçbir yanıtta görülmediği için tutor modeli hâlâ çıkarım.

---

## 12. PDF → ders hattı: kanıtlar (son tur)

Sayfa açılışta PDF yükleme ekranını gösteriyor, dosya okunduktan sonra
görev kartları geliyor; "Starten" tahtayı temizleyip dersi **sol üst köşeden**
başlatıyor. Aşağıdaki sayılar `check-math-pdf-flow.mjs` ile gerçek tarayıcıda
(CDP, 1400×900) alındı; test PDF'i `/tmp/math-homework.pdf`.

### 12.1 Çıkarma ve görev ayırma

| Ölçüm | Sonuç |
|---|---|
| Sayfa | 1 |
| Okunan karakter | 259 (stdlib PDF metin çıkarma, yeni bağımlılık yok) |
| Bulunan görev | 3 |
| Görev 1 | **Schnittpunkte** — `f(x)=2x^2 + 3x + 7 ; g(x)=4x^2 - 5x - 3` |
| Görev 2 | **Nullstellen** — `x^2 - 9x + 8 = 0` |
| Görev 3 | **Ungleichung** — `-0,1x^2 + 3x - 6 > -0,2x^2 + 5x, 0 <= x <= 24` |

### 12.2 Ders akışı ve doğruluk

| Görev | Tur | Tahtadaki sonuç | Model hatası | Fallback | Konsol |
|---|---|---|---|---|---|
| Schnittpunkte | 10 | `x1 = -1, x2 = 5`, `P1(-1\|6)`, `P2(5\|72)` | 0 | 0 | 0 hata |
| Nullstellen | 7 | `x1 = 1, x2 = 8`, `S(4,5 \| -12,25)`, `P1(1\|0)`, `P2(8\|0)` | 0 | 0 | 0 hata |
| Ungleichung | 8 | `x < -2,65 oder x > 22,65` → `22,65 < x ≤ 24` | 0 | 0 | 0 hata |

Ortak ölçümler: `katexErrors = 0`, `exceptions = 0`, "fit" sonrası tahta
dışında kalan element **0**, tahta tek sütun hâlinde yukarıdan aşağı akıyor,
grafik varsa sağdaki ayrı sütuna geçiyor. Mobil (414 px): yatay taşma yok,
tahta %45–50 ölçekle sığıyor, alt bilgi balonu mobilde gizleniyor.

### 12.3 Sayılar nereden geliyor (tek hata düzeltmesi)

İlk sürümde model serbestçe hesaplıyordu ve bir derste
`x1,2 = 2 ± √9 = 2 ± 3` yazıp **sonraki satırda** `2 ± 6 ⇒ x1 = 8, x2 = -4`
diyor, `f(8) = 167` gibi yanlış bir son nokta üretiyordu. Bu yüzden:

1. `/api/math/tutor` artık görevin normalize edilmiş hâlini (`expr`) de alıyor.
2. Sunucu `math_agent.analyse(expr)` ile **kesin** çözümü (`Fraction`) hesaplayıp
   istem içine "Geprüfter Rechenweg" bloğu olarak koyuyor; model her turda bu
   listeden **tek** satır yazıyor, kendi ara sayısını uydurmuyor.
3. Son turda (`done=true`) modelin "result" satırları atılıp yerine çekirdeğin
   doğrulanmış sonuç satırları yazılıyor (`_final_guard`).

Bunun pratik etkisi: aynı görevde iki ayrı koşuda tahtada `-1/5` ve `1/8`
kökleri, `(-1|6)`, `(5|72)`, `22,65 < x ≤ 24` değerleri birebir çıktı.

### 12.4 Gecikme ve dayanıklılık

| Ayar | Değer |
|---|---|
| Birincil model | `nvidia/nemotron-3-ultra-550b-a55b` (NVIDIA NIM) |
| Düşünme (reasoning) | `chat_template_kwargs={"thinking": false}` — 20–40 s → 4–9 s |
| Tur süresi (ölçülen) | 4,1–19 s; tipik 5–9 s |
| Deneme | Tur başına 2 kez aynı model, tekrar arasında 1 s |
| Tur bütçesi | 48 s (`MATH_BUDGET`), model zaman aşımı 22 s (`MATH_TIMEOUT_NIM`) |
| İstemci | 55 s'de isteği keser, deterministik plana düşer |
| Son 4 tam koşu | 0 fallback (öncesinde 503 dalgalanmalarında 1–3 fallback görülüyordu) |

### 12.5 Tahta görünümü

Studdy ekran görüntüsüne (`screenshots/studdyai-canvas.png`) göre üç nokta
alındı: krem kâğıt zemin, el yazısı başlık, sonuçların renkli kutu içinde
olması. Çerçeveli "space" kutuları, noktalı ızgara ve etiketler kaldırıldı;
başlık `Bradley Hand` yığınıyla, nihai sonuç satırı `#e2f4ea` zeminli
`el-result` sınıfıyla çiziliyor. Aynı satır iki kez gelirse **görünen metin**
karşılaştırılarak atlanıyor (`\le` / `\leq`, `-` / `−`, `{x}` / `x` farkları
tek satır sayılıyor).
