# Studdy AI nasıl çalışıyor — mekanizma ve pedagoji

Bu dosya "ne yapıyor" değil, **"nasıl yapıyor"** sorusunun cevabı. Her iddia ya canlı kayda ya da istemci koduna dayanıyor; ikisi ayrı ayrı işaretli.

---

## 1. Üç ayrı model, üç ayrı iş

Studdy tek bir "AI tutor" değil; **üç farklı model katmanı** var.

| Görev | Model | Nerede çalışıyor | Kanıt |
|---|---|---|---|
| Ders kurgusu + komut üretimi (yaz, çiz, konuş) | **Gemini 3 Flash** (`gemini-3-flash-preview`, Vertex) | Sunucu (Vercel → iad1) | Kod: model kataloğu, "Production model (first rung)" |
| Yedek | **Gemini 3.1 Flash-Lite** | Vertex | "second and third rungs" |
| Vertex kesintisi | **Gemini 3 Flash · Gemini Developer API** | Google API | "a way around a Vertex-wide outage" |
| Görsel (el çizimi illüstrasyonlar) | **Bilinmiyor** — istemciye hiç sızmıyor | Sunucu | 44 chunk'ta tek bir görsel model adı yok |
| Ses (TTS) | **Azure DragonHD** (`de-DE-Florian:DragonHDLatestNeural`) | Azure | 41 gerçek istek |
| Ses (STT) | **Azure Speech SDK 1.47.0** | Azure eastus | 312 WebSocket çerçevesi |

### 1.1 Metin modelinin ayarları (kodda sabit)

```js
{ includeThoughts: false, thinkingLevel: "MINIMAL" }
signatureReplay: "as-recorded"
```

- **`thinkingLevel: "MINIMAL"`** — düşünme bütçesi kısılmış. Sebep belli: tur başına ilk çıktı gecikmesi 2 saniyenin altında olmalı. Pedagojik derinlik değil, **akıcılık** optimize edilmiş.
- **`signatureReplay: "as-recorded"`** — Gemini'nin "thought signature" mekanizması: her turun düşünme imzası geri gönderiliyor, model kaldığı yerden akıl yürütmeye devam ediyor. Yani ders boyunca **tek bir uzun akıl yürütme zinciri** taşınıyor, her tur sıfırdan başlamıyor.
- `thinkingLevel: "MINIMAL"` seçilmiş olması, mimarinin neden **komut protokolüne** yaslandığını da açıklıyor: modelin "derin düşünmesine" güvenmek yerine, çıktıyı yapılandırılmış eylemlere zorluyorlar.

### 1.2 Neden Gemini?

Katalogda `gpt-6.1-sol`, `claude-opus-5-5`, `claude-fable-5.1`, `deepseek-v4-flash` da var — ama notlar açıkça öğrenci trafiğinde kullanılmadıklarını söylüyor:

> `deepseek-v4-flash`: *"Ruled out for students; the open-model example. Sometimes calls board actions as tools (a wasted round); slow to its first output (p90 104 s)."*

Bu tek cümle ürünün gerçek kısıtını gösteriyor: **p90 ilk-çıktı süresi**. Pedagojik kalite ikinci planda; 104 saniye bekleyen öğrenci dersten kopar.

---

## 2. Kimya görselleri nasıl üretildi — iki ayrı yol

Ders sırasında iki tür görsel gördün, ve bunlar **tamamen farklı mekanizmalarla** üretildi.

### Yol A — Model SVG'yi kendisi yazdı

Formül üçgeni (`formula_triangle`) 23 parça halinde, **karakter karakter** aktı:

```json
{"_stream":"svg","elementId":"formula_triangle","chunk":"<svg xmlns=\"...\" viewBox=\"0 0 200 200\">\n  <path d=\"M100 15 L185 180 H15 Z\" .../>"}
{"_stream":"svg","elementId":"formula_triangle","chunk":"  <g stroke=\"#ABABAB\" ...>"}
```

Burada **ayrı bir görsel modeli yok**. Metin modeli doğrudan SVG kodu yazdı, istemci parçaları birleştirip tahtaya bastı.

Neden: formül üçgeni, koordinat düzlemi, geometri — bunlarda **kesinlik** gerekiyor. Difüzyon modeli "yaklaşık" çizer; üçgenin kenarortayı yanlış yerde olursa ders bozulur.

### Yol B — Ayrı görsel modeli, modelin yazdığı "çizim brief'i" ile

Mol analojisi (`mol_analogy`) PNG olarak geldi. Ama önce model **doğal dilde bir çizim talimatı** yazdı:

```json
{
  "name": "draw",
  "args": {
    "id": "mol_analogy",
    "prompt": "A large box containing many tiny dots representing atoms, labeled '1 Mol', next to a small egg carton labeled '1 Dutzend (12)'. Simplified doodle style.",
    "isStructured": false,
    "maxDimensions": { "maxWidth": 300, "maxHeight": 250 },
    "position": { "spaceId": "mol_basics", "columnId": "mol_intro" }
  }
}
```

Dikkat: bu prompt **şablon değil**. "egg carton", "simplified doodle style", "labeled '1 Mol'" — hepsi modelin o anki pedagojik amacına göre yazdığı sanat yönetimi. Yani öğretmen tahtaya çizerken nasıl "şöyle bir kutu, içinde noktalar" diye düşünüyorsa, model de aynısını yapıyor.

Görsel üretimi **iki fazlı** ve asenkron:

```json
// faz 1 — komut anında: eleman var, içerik yok
{ "name":"whiteboard_element_added", "elementId":"mol_analogy",
  "contentType":"image-file", "content":"", "size":{"width":528,"height":288} }

// faz 2 — görsel hazır: aynı eleman güncellenir
{ "name":"whiteboard_element_edited", "elementId":"mol_analogy",
  "content":"https://umcvdgstciybysimigxa.supabase.co/storage/v1/object/public/generated_images/1793190500231-mol_analogy.png" }
```

Bu tasarım önemli: **akış beklemiyor**. Eleman önce tahtada yerini alıyor (boş kutu), ders devam ediyor, görsel 2–4 saniye sonra yerine oturuyor. Algılanan hız, gerçek hızdan yüksek.

**Boyut hilesi:** model `300×250` istedi, sunucu `528×288` verdi. Nihai boyutu sunucu politikası belirliyor — modelin görsel kompozisyonu bozmasını engelliyor.

### Yol C — Karar kimde?

Metin modeli. Komut şemasında üç dal var:

| Dal | Ne zaman | Sonuç |
|---|---|---|
| `svg` alanı dolu | Kesin geometri | `svg-drawing-inline` — doğrudan tahtaya |
| `filePath` + `fileType:"svg"` | Sunucu SVG üretmiş | `svg-drawing-file` |
| `fileType:"png"`, `filePath` yok | İllüstrasyon | Boş eleman + async görsel |

Yani model, "bu bir kavram mı yoksa bir şekil mi" kararını **her komutta yeniden** veriyor. Sabit bir kural yok.

### Neden görsel modelinin adı yok?

44 chunk'ın hiçbirinde `dall-e`, `flux`, `imagen`, `gpt-image`, `stable-diffusion` geçmiyor. Tek `flux` eşleşmesi `flux-general-en` — o da Deepgram STT modeli.

Yani görsel üretimi **tamamen sunucu tarafında**, istemci sadece `prompt` yolluyor ve URL alıyor. Bu bilinçli bir soyutlama: model değiştirmek istemci güncellemesi gerektirmiyor. (Aynı şey metin modeli için geçerli değil — orada katalog istemcide, çünkü yetkili kullanıcı model seçebiliyor.)

---

## 3. Dersi nasıl kurguluyor

### 3.1 Önce teşhis, sonra plan

Oturum açılır açılmaz tutor **ders anlatmaya başlamadı**. Önce tahtaya dört soru yazdı:

```
1. Wann ist die Prüfung?
2. Welche Themen kommen dran?
3. Wo fühlst du dich unsicher, wo sicher?
4. Erst Wiederholung oder direkt Übungsaufgaben?
```

Sonra her soruyu **sırayla** sesli sordu, cevap bekledi. Yani:

**teşhis → hedef → plan → içerik**

Bu, öğretmen eğitiminde "advance organizer" denilen şeyin tahtaya yazılmış hali.

### 3.2 Tahta = dersin iskeleti

Tahta rastgele değil; **`spaceId` / `columnId`** ile bölümlenmiş. Gözlenen yapı:

```
space: session_setup  → column: overview      (başlık, öğrenci cevapları, sorular)
space: mol_basics     → column: mol_intro     (tanım, değişkenler, PSE)
                      → column: visual_aid    (çizimler, formül, üçgen)
```

- **space** = ders bölümü (konu)
- **column** = o bölümün adımı (slayt gibi)

Ve bu isimleri **model uyduruyor**: `mol_basics`, `visual_aid`, `session_setup`. Kodda sabit değil. Yani dersin görsel mimarisi her oturumda model tarafından kuruluyor.

Planı da tahtaya yazdı (`lesson_plan_updated` komutu):

```
1. Was ist ein Mol? (kurz)
2. Molare Masse M aus dem Periodensystem
3. Die Hauptformel: n = m/M
4. Teilchenzahl: N = n · N_A
5. Übungsaufgaben wie in der Prüfung
```

Ve **planı harfiyen uyguladı**. 9 tur boyunca sıra hiç bozulmadı. Bu, modelin serbest sohbet etmediğini, bir müfredat yürüttüğünü gösteriyor.

### 3.3 Tur döngüsü: yaz → söyle → sor → bekle

Her tur aynı iskelet:

| Adım | Komut | Ne oluyor |
|---|---|---|
| 1 | `write_text` / `write_latex` | Tahtaya kavram yazılır |
| 2 | `highlight` / `circle_annotate` | Kritik kelime işaretlenir |
| 3 | `speak` | Aynı içerik sesli anlatılır |
| 4 | `speak` (soru) | Kontrol sorusu sorulur |
| 5 | — | **Bekler** (kullanıcı cevabı gelene kadar) |

Adım 1–3 neredeyse aynı anda. Buna çift kanal (dual coding) deniyor: gözle okunan + kulakla duyulan aynı bilgi, tek kanaldan gelen bilgiden belirgin şekilde daha iyi hatırlanıyor.

### 3.4 Hafıza: event log + sıkıştırma

Her şey event olarak yazılıyor. Uzun derslerde son **24 model turu** tutuluyor, öncesi `collapse-context` ile özete sıkıştırılıyor. Model `collapsed_block_expanded` komutuyla özet bloğunu geri açabiliyor — yani "geçen derste ne demiştim" diyebiliyor.

Ayrıca istemci, sunucuya **sistem talimatı** gönderiyor:

```json
{ "name":"session_resumed",
  "text":"[SYSTEM] The user has previously ended the tutoring session and now they are back to resume it. Greet them and ease into it. [END SYSTEM]" }
```

Yani "şu anda ne yapmalısın" bilgisi sunucuda değil, **istemcide** kuruluyor ve event metnine gömülüyor.

### 3.5 Zaman baskısı tasarıma gömülü

- Ders başına **10 dakika**, günlük **2 ders**, toplam **15 dakika** ücretsiz.
- Tutor bunu **biliyor ve dile getiriyor**: *"kein Rumtrödeln, wir machen das direkt prüfungsfit"*, *"Ganz kurz, damit wir die Zeit gut nutzen"*.
- 66 saniye hareketsizlik → ders otomatik duraklıyor.

---

## 4. Pedagojik analiz

### 4.1 İyi yaptığı şeyler (kanıtlı)

| Teknik | Kanıt | Neden etkili |
|---|---|---|
| **Advance organizer** | Planı tahtaya yazdı, sonra uyguladı | Öğrenci nereye gittiğini biliyor; bilişsel yük azalıyor |
| **Çift kanal (dual coding)** | `write_text` + `speak` aynı içerik | Görsel + işitsel kodlama, hatırlama artıyor |
| **Analoji** | *"Ein Mol ist wie ein Dutzend"* → sonra *"aber viel größer"* | Soyut sayıyı tanıdık çerçeveye oturtuyor |
| **Aşamalı destek (scaffolding)** | *"Die Zahl liegt knapp unter 16"* → olmazsa *"ein paar Kästchen weiter rechts"* | Aynı soruyu 3 farklı ipucuyla tekrar sordu |
| **Retrieval practice** | *"Was steht bei Sauerstoff?"* | Cevabı söylemek yerine öğrenciye hatırlattı |
| **Anında geri bildirim** | *"Ah, fast! Die sechs ist die Ordnungszahl"* | Hata ile doğruyu aynı cümlede ayırdı |
| **Hata çerçeveleme** | Hiç "falsch" demedi | Kaygıyı düşürüyor; öğrenci denemeye devam ediyor |
| **Mnemonik** | Formeldreieck ("Chef" formül) | Kullanılacak formülü seçmeyi görselleştiriyor |
| **Adaptasyon** | *"ich weiss nicht"* → tahtaya çizip basitleştirdi | Aynı şeyi yüksek sesle tekrar etmedi, **yöntem değiştirdi** |

En dikkat çekici an şuydu: öğrenci *"ich weiss nicht"* dediğinde tutor cevabı vermedi, **tahtaya Sauerstoff hücresini çizdi** ve aynı bilgiyi başka bir kanaldan verdi. Bu, gerçek öğretmen davranışı.

### 4.2 Zayıf yaptığı şeyler (kanıtlı)

| Sorun | Kanıt | Pedagojik sonuç |
|---|---|---|
| **Ustalık ölçümü yok** | "Quick Check" soruları doğru/yanlış olarak kaydedilmiyor | Ders bitince "öğrendi mi" sorusunun cevabı yok |
| **Hata teşhisi yüzeysel** | Öğrenci `12,01` dedi → *"yine karbona döndün"* | **Neden** karıştırdığını araştırmıyor; sadece yönlendiriyor |
| **Dil hatasını düzeltmiyor** | `es it morgern` yazıldı, tahtaya düzgün geçti, hiç söylenmedi | Dil öğrenen için sessiz hata birikmesi |
| **Tek strateji tekrarı** | Aynı kavramı 3 kez *aynı* görsel dille anlattı | Alternatif açıklama yolu yok |
| **10 dakika kısıtı** | Tutor *"kein Rumtrödeln"* diyor | Derin öğrenme için kısa; ürün kısıtı pedagojiyi şekillendiriyor |
| **Bağımlılık riski** | Her adımda tutor yönlendiriyor, öğrenci kendi başına denemiyor | "Productive struggle" yok |
| **Değerlendirme yok** | Sınav benzeri ölçüm turu planın 5. adımı ama süre yetmedi | Plan ile süre çelişiyor |

### 4.3 Asıl pedagojik teşhis

Bu bir **Sokratik tutor değil**. Sokratik yöntemde öğretmen soru sorar, öğrenci kendi cevabını inşa eder. Burada model:

1. Kavramı **açıklıyor** (didaktik)
2. Tahtaya **gösteriyor** (görsel)
3. Küçük bir **kontrol sorusu** soruyor (tek adım)
4. Doğruysa ilerliyor, yanlışsa **ipucu veriyor**

Yani model **"açıkla–göster–sor–düzelt"** döngüsü. Bu, iyi bir özel ders öğretmeninin yaptığı şey; Sokratik değil ama etkili.

Fark önemli: Sokratik yöntem yavaş ve bilişsel olarak yorucu. 10 dakikalık ücretsiz derste yavaş yöntem işe yaramaz. Ürün, **kısıtına uygun pedagoji** seçmiş.

### 4.4 Modelin "kişiliği" pedagojiyi nasıl şekillendiriyor

Tutor persona'sı (kodda sabit, `alfrenzo`):

> *"warm and friendly, encouraging but also to the point, cool, smart and sometimes tough... challenges the student just enough so that they move one step further"*

Bu, gözlenen davranışla birebir örtüşüyor:
- "warm": *"Gar kein Problem, dafür bin ich ja da"*
- "to the point": *"kein Rumtrödeln"*
- "sometimes tough": *"Hmh.. da bist du gerade wieder bei Kohlenstoff gelandet"*
- "one step further": her seferinde tek ipucu, cevabı vermiyor

Yani persona süsleme değil; **pedagojik stratejinin parametresi**. `rubie` personası farklı bir öğretmen tipi (daha sıcak, daha sabırlı) ve aynı müfredatı farklı tempoda yürütüyor.

---

## 5. Tahta nasıl yerleşiyor — anlam modelde, geometri istemcide

Tahtanın "hangi yöne gideceği" sorusunun cevabı ikiye ayrılıyor, ve ayrım kesin:

| Karar | Kim veriyor | Elinde ne var |
|---|---|---|
| **Anlam**: bu içerik hangi bölümün, hangi adımın parçası | **Model** | `{spaceId, columnId}` — başka hiçbir şey |
| **Geometri**: x/y, sütun genişliği, boşluk, satır kırılması | **İstemci**, deterministik | Hiçbiri modele gitmiyor |

### 5.1 Model piksel görmüyor

40 dakikalık iki derste geçen **37 elementin tamamı** tarandı. Event log'daki `position` nesnesinde kullanılan anahtarlar:

```
position keys ever used: {'spaceId', 'columnId'}
```

`x`, `y`, genişlik, sıra numarası — hiçbiri kalıcı kayda yazılmıyor. Sonuç: **model piksel görmediği için pikseli kaybedemez.** "Elementi yanlış yere koydu" diye bir hata sınıfı yapısal olarak yok.

### 5.2 Yerleşim algoritması: sabit sayılar, 30 satır kod

`figboard` bileşeni (`s29.js`) her render'da event log'u baştan tarayıp tahtayı yeniden kuruyor:

```js
{ defaultColumnWidth: 480, elementGap: 24, columnGap: 40,
  spaceGap: 80, rowGap: 160, maxSpacesPerRow: 3 }
```

| Kural | Uygulama |
|---|---|
| Sütun içi | Elementler **dikey** yığın; yükseklik gerçek render boyutundan ölçülüyor, aralarına 24 px |
| Sütunlar | Soldan sağa, 40 px boşluk, genişlik `max(480, en geniş element)` |
| Space'ler | Soldan sağa, 80 px boşluk; **3 space sonra satır atlanır** (+160 px) |
| Sıra | Elementin **ilk göründüğü an**: `turnIndex → orderIndex → subOrderIndex` |

Üst üste binme matematiksel olarak imkânsız: her element bir öncekinin ölçülen yüksekliği kadar aşağı itiliyor. Tahta **sabit bir okuma yönünde** büyüyor — soldan sağa, sonra aşağı. Model "nereye gideyim" demiyor; akış onu götürüyor.

### 5.3 Düzenleme elementi yerinden oynatmıyor

```js
// ilk-görünme indeksi korunur; yalnızca space/column DEĞİŞTİYSE yeniden damgalanır
s && !u && n.has(i.elementId) || n.set(i.elementId, { turnIndex, ... })
```

Bir element sonradan güncellenirse (görsel placeholder → PNG, metin düzeltmesi) **eski yerinde kalıyor**. Sadece başka sütuna taşınırsa hedef sütunun sonuna düşüyor. Bu yüzden asenkron görsel üretimi düzeni bozmuyor: boş kutu yerini alıyor, ~8 saniye sonra içi doluyor, hiçbir şey kaymıyor.

### 5.4 Kamera: hep en son değişene

İstemci "en son ne değişti" sorusunu bir **öncelik sırasıyla** cevaplıyor:

```
yeni element > düzenlenen element > yeni anotasyon > boyutu değişen
```

Sonra hedefin imzasını (`reason|elementId|size|annotations|content`) karşılaştırıp değiştiyse kamerayı oraya uçuruyor. Hedef ilk space/sütun dışındaysa ayrıca `onNewColumnPan()` tetikleniyor. Yani **kamera hep ileri gidiyor**. Geri dönüş otomatik değil: kullanıcı kaydırıp uzaklaşırsa ekranda **"Follow me"** butonu beliriyor (`refocusAvailable` → `requestRefocus()`). Ekip drift'in farkında ve manuel bir çıkış koymuş.

### 5.5 Uzarsa ne oluyor — ölçüm

İki gerçek ders, aynı algoritmayla yeniden kuruldu:

| Ders | Tur | Space | Sütun | Element | Tahmini tuval | Viewport |
|---|---|---|---|---|---|---|
| Kimya (mol) | ~9 | 2 | 4 | 14 | **~2160 × 1260 px** | ~1440×800 |
| Almanca (n-Deklination) | 21 | 1 | **7** | 23 | **~3640 × 560 px** | ~1440×800 |

Almanca dersinde tek space içinde 7 sütun birikmiş: `compare → practice → turkish_explanation → turkish_rules → summary → extra_practice → rules_deep_dive`. 3.640 px = **2,5 ekran genişliği**. Ve `maxSpacesPerRow: 3` yalnızca *space*leri satıra sarıyor — **tek space'in sütunları sınırsız sağa gidiyor**.

Daha önemlisi, 40 dakikada:

```
whiteboard_element_removed: 0
whiteboard_cleared:        0
erase komutu:              0
```

**Tahta sadece büyüdü, hiç temizlenmedi.** `erase` komutu kodda var (`eraseAll` → `whiteboard_cleared`), yani "yeni sayfa aç" yeteneği mevcut — ama **kullanma politikası yok**. Model 21 turda bir kez olsun eski içeriği silmeyi düşünmedi.

### 5.6 Model "ne neredeydi" diye arar mı

Hayır — arayacak bir şey yok. İki mekanizma var, ikisi de arama değil:

**a) `resolveElementUpsertEventName(elementId)`** — log'u geriye doğru tarayıp "bu id daha önce eklendi mi" diye bakıyor. Semantik arama değil, `elementId` ile birebir eşleşme. `whiteboard_cleared` veya aynı id'nin `removed`'ına rastlarsa duruyor. Maliyet O(n), n ≈ 60–100 event.

**b) Model tahtayı event log metninden yeniden kuruyor.** Sunucu durumsuz; her turda log'u okuyup context yapıyor. Yani tahta **görsel olarak değil, metin olarak** hatırlanıyor:

```
"mol_analogy" → { spaceId: "mol_basics", columnId: "mol_intro" }, contentType: "image-file"
```

İşin ironisi: kodda `getLastUsedPosition()` var — "en son hangi space/sütuna yazdım" sorusunun tam cevabı. **Hiçbir yerden çağrılmıyor.** Ölü kod. İstemci bu bilgiyi biliyor ama modele hiç vermiyor.

### 5.7 Gerçek riskler — dağınıklık değil, üç başka şey

| Risk | Kanıt | Sonuç |
|---|---|---|
| **Sütun sırası = kronoloji, niyet değil** | Almanca dersinde `summary` 5., `rules_deep_dive` 7. sırada — konu sırasına göre değil, dokunulma sırasına göre | Sonradan eklenen sütun **en sağa** düşer; özet, anlattığı konudan 3 ekran uzakta kalabilir |
| **Tek yönlü büyüme** | 0 erase, 0 clear, kamera sadece ileri | Öğrenci "az önce ne yazmıştı" diye **geri kaydırmak** zorunda; tutor bunu yapamıyor |
| **Sabit 480 px sütun** | `boardDimensions` yalnızca metadata olarak bir kez gönderiliyor; layout sabitleri viewport'a duyarlı değil | Telefonda (390 px) sütun ekranın 1,2 katı — zoom mecburi |

Bunların hiçbiri "dağınıklık" değil. Tahta **hiç dağılmıyor**; sadece **tek yönde ve temizliksiz** büyüyor. Fark önemli: dağınıklık düzeltilebilir bir hata, tek yönlü büyüme bir **tasarım borcu**.

### 5.8 Düzeltme yolları

| Hamle | Ne değişir | Neden |
|---|---|---|
| **Sütun sırasını model açıkça bildirsin** (`columns:[{id, order}]`) | Kronoloji yerine niyet sırası | Özet, anlattığı konunun yanında durur |
| **Sütun = sayfa, `erase` ile sayfa devri** | 7 sütun yerine 2–3 sütunluk sayfalar | Kamera geri gitmek zorunda kalmaz, bilişsel yük sabit kalır |
| **Tahta indeksi prompt'a** (`{"mol_basics/mol_intro": 3 element}`) | Model "ne nerede"yi log tarayarak değil, hazır özetten cevaplar | Context şişmesini de yavaşlatır |
| **`getLastUsedPosition()` gerçekten bağlansın** | Model "kaldığım sütuna devam" diyebilir | Kod zaten yazılmış, sadece çağrılmıyor |

Asıl mimari ders: **yerleşimi modele bırakmak doğru karar.** Model piksel bilseydi her turda "bu 528 px nereye sığar" diye düşünmesi gerekirdi — `thinkingLevel: "MINIMAL"` ile bu imkânsız. Onun yerine modele **semantik bir adres** (`space/column`) verip geometriyi deterministik koda bırakmışlar. Doğru soyutlama. Eksik olan tek şey: bu adres uzayının **bir sınırı ve bir temizlik kuralı** olmaması.

---

## 6. Tek cümlede

Studdy'nin yaptığı şey "kimya anlatan bir model" değil; **bir öğretmenin ders defterini, tahtasını ve sesini ayrı ayrı modelleyip, hepsini tek bir olay günlüğünden üretmek**. Model, her turda ne söyleyeceğine değil, **tahtaya ne yazacağına** karar veriyor — ve o karar kalıcı bir kayda dönüşüyor. Pedagoji bu kaydın üzerine kuruluyor: plan tahtada, ipucu tahtada, düzeltme tahtada.
