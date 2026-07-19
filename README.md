# AgroVision

> Yapay Zeka ve Teknoloji Akademisi — Bootcamp 2026, 5. Akademi Dönemi

---

## 👥 Takım Bilgileri

| İsim                 | Rol           |
| -------------------- | ------------- |
| İrem Ayça Uçankale   | Product Owner |
| Enver Buğrahan Alyüz | Scrum Master  |
| Esma Tuğçe Kireşci   | Backend Developer     |
| Rukiye Dişçi         | ML Developer      |
| fatihyalcimin        | Frontend Developer     |

---

## 🌱 Ürün İle İlgili Bilgiler

**Ürün İsmi:** AgroVision

**Ürün Açıklaması:**
> Kurutulmuş incirlerde aflatoksin (Aspergillus flavus/parasiticus kaynaklı mikotoksin) kontaminasyonunu, UV ışık altında çekilen canlı kamera görüntüsü üzerinden **YOLOv11 tabanlı gerçek zamanlı tespit** (bounding box) ile belirleyen ve aflatoksin regülasyonları/kalite kriterleri hakkında bir **RAG (Retrieval-Augmented Generation) bilgi asistanı** ile destekleyen bir kalite kontrol sistemi.

**Ürün Özellikleri:**
- Canlı kamera akışı (live stream) üzerinden gerçek zamanlı tespit, tespit kutuları (bounding box) ile görselleştirme
- Sağ panelde oturum istatistikleri, güven eşiği (confidence threshold) ayarlanabilir slider, son taramalar tablosu
- Backend API üzerinden YOLOv11 modelinin servis edilmesi
- Aflatoksin regülasyonları, incir kalite kriterleri ve tarım mevzuatına dayanan RAG destekli bilgi/soru-cevap sistemi

**Hedef Kitle:**
- Ege Bölgesi kurutulmuş incir üreticileri ve ihracatçıları
- Tarım kooperatifleri (kalite kontrol birimleri)
- İhracat öncesi kalite denetimi yapan aracı kurumlar

**Product Backlog URL:** (https://tugcekiresci.atlassian.net/jira/software/projects/SCRUM/boards/1/backlog)

---

## 🏃 Sprint 1 (19 Haziran – 5 Temmuz 2026)

### Backlog Dağıtma Mantığı
> Bu sprint'te henüz teknik geliştirme backlog'u oluşturulmadı. Sprint 1, **proje scope'unu belirleme ve sektör araştırması** sprint'i olarak planlandı. Yapılan araştırma görevleri Bucket System ile kabaca gruplandırıldı (tartışmadan, göz kararıyla):

- [x] Kova (Küçük efor): Tarım+AI alanında genel literatür/pazar taraması yapmak
- [x] Kova (Orta efor): Aday problem alanlarını listelemek ve kısa artı/eksi analizi çıkarmak
- [x] Kova (Büyük efor): Aday alanlardan birini seçip hedef kitle + kaynak (veri seti, API vb.) uygunluğunu detaylı incelemek
- [ ] Kova (Orta efor): Seçilen fikir için ilk teknik backlog'u oluşturmak (Sprint 2'ye devretti)

**Aday problem alanları (araştırma sonucu):**
1. Bitki hastalığı/zararlı teşhisi (görsel veri ile CNN tabanlı tespit)
2. Ürün verim/fiyat tahmini (zaman serisi + piyasa verisi)
3. Sulama/gübreleme öneri asistanı (sensör verisi + LLM agent)
4. İncirde UV-floresan tabanlı aflatoksin tespiti (Sprint 2'de seçilen fikir)

### Daily Scrum Notları

> Sprint 1 boyunca ekip henüz düzenli daily formatına geçmediği için notlar sınırlıdır. Sprint 2'den itibaren her gün kısa yazılı check-in (Slack üzerinden) planlanmaktadır.

- 19 Haziran: Takım tanışma, bootcamp kılavuzu ortak okuma
- 25 Haziran: Aday problem alanları üzerine kısa değerlendirme
- 2 Temmuz: Sprint 1 kapanış, Sprint 2 planlaması için ön görüşme

### Sprint Board Updates

![Sprint Board](#)

<img width="1522" height="833" alt="WhatsApp Image 2026-07-19 at 21 34 06" src="https://github.com/user-attachments/assets/ca1a25d8-884e-444f-9654-ff6d36ec4267" />
<img width="1517" height="817" alt="WhatsApp Image 2026-07-19 at 21 34 07" src="https://github.com/user-attachments/assets/0cab2584-d0ef-409b-b28d-68ffd36a0b41" />
<img width="1493" height="837" alt="WhatsApp Image 2026-07-19 at 21 41 48" src="https://github.com/user-attachments/assets/fd54c720-6040-4f38-b936-4f377ff4dcfc" />




### Ürün Durumu

![Ürün Durumu](#)
> Bu aşamada geliştirilen bir ürün/prototip bulunmamaktadır. Sprint 1 çıktısı, dört aday problem alanının kısa değerlendirmesidir.


### Sprint Review
- **Tamamlanan:** Tarım+AI alanında genel araştırma yapıldı, aday problem alanları belirlendi.
- **Tamamlanamayan:** Tek bir fikre karar verilemedi, teknik backlog oluşturulamadı, GitHub repo kurulumu yapılamadı.
- **Neden:** Takım üyelerinin bootcamp'in ilk haftasında yoğunlukları farklıydı, ilk toplanma ve senkronizasyon beklenenden geç oldu.

### Sprint Retrospective

| İyi Gitti | İyileştirilmeli | Aksiyon |
|---|---|---|
| Takım hızlıca birkaç makul aday fikir üzerinde hemfikir oldu | Karar alma süreci çok yavaş ilerledi, fikir netleşmedi | Sprint 2'nin ilk günlerinde fikir kesinleştirilecek |
| İletişim kanalı (Slack) sorunsuz kuruldu | Düzenli daily/checkpoint alışkanlığı oluşmadı | Sprint 2'de sabit saatli kısa daily check-in başlatılacak |
| — | GitHub repo ve Jira board kurulumu gecikti | Sprint 2'nin ilk günü repo + Jira board kurulacak (Scrum Master sorumluluğunda) |

---

---

## 🏃 Sprint 2 (6 Temmuz – 19 Temmuz 2026)

### Backlog Dağıtma Mantığı
> Sprint 2'nin ilk günlerinde takım, aday fikirler arasından **"İncirde UV-Floresan Tabanlı Aflatoksin Tespiti"** fikrinde karar kıldı. Fikir netleşir netleşmez GitHub repo public yapıldı ve **Jira** üzerinde ("My Software Team" projesi) backlog board'u kuruldu.

Sprint backlog'u önce organizasyonel kararlarla başladı, ardından teknik geliştirme story'leri eklendi:

**Organizasyonel/planlama story'leri:**
- Proje konusu belirlenmeli (SCRUM-1)
- Takım ismi belirlenmeli (SCRUM-2)
- Ürün ismi belirlenmeli (SCRUM-3)
- Projenin frontend kısmını kim yapacağı belirlenmeli (SCRUM-4)
- Projenin backend kısmını kim yapacağı belirlenmeli (SCRUM-5)
- Projedeki RAG kısmını kim yapacağı belirlenmeli (SCRUM-6)
- Projedeki rapor kısmını kim yapacağı belirlenmeli (SCRUM-7)
- Veri seti araştırılmalı (SCRUM-8)

**Teknik geliştirme story'leri:**
- Canlı Kamera Akışı (Live Stream) ekranı ve bounding box bileşeninin arayüz kodlaması (SCRUM-9)
- Sağ paneldeki Oturum İstatistikleri / Güven Eşiği Slider'ı / Son Taramalar tablosu UI bileşenleri (SCRUM-10)
- Backend API projesinin kurulması ve konfigürasyon ayarları (SCRUM-11)
- YOLOv11 modelinin backend servisine entegre edilmesi ve ilk çıkarım (inference) testleri (SCRUM-12)
- RAG sisteminde kullanılacak bilgi tabanının (aflatoksin regülasyonları, incir kalite kriterleri, tarım mevzuatları) toplanması ve metin temizleme işlemleri (SCRUM-13)
- 
**Story Point Tahminleri (Fibonacci):**

| Story | Puan | Gerekçe |
|---|---|---|
| Canlı Kamera Akışı UI + bounding box (SCRUM-9) | 8 | Gerçek zamanlı video akışı + üzerine dinamik çizim gerektiriyor, orta-yüksek karmaşıklık |
| Sağ panel UI bileşenleri (SCRUM-10) | 5 | Üç ayrı bileşen (istatistik, slider, tablo) ama backend'e bağımlılığı düşük, standart UI işi |
| Backend API kurulumu (SCRUM-11) | 5 | Proje iskeleti + konfigürasyon, bilinen bir süreç ama entegrasyon noktaları var |
| YOLOv11 model entegrasyonu (SCRUM-12) | 13 | Model + backend entegrasyonu + inference testleri, belirsizlik yüksek |
| RAG bilgi tabanı hazırlığı (SCRUM-13) | 8 | Mevzuat/kriter metinlerinin toplanması + temizleme, veri kalitesi belirsiz |

**Toplam Sprint 2 backlog puanı:** 39

### Daily Scrum Notları
> Slack'te `#daily-checkin` kanalı üzerinden yazılı check-in formatına geçildi (Dün / Bugün / Blocker). Sprint 2 boyunca detaylı günlük kayıtlar henüz Jira/Slack üzerinden README'ye aktarılmadı.


### Sprint Board Updates
> Jira ("My Software Team" projesi) üzerinde **To Do / In Progress / Done** kolonları kullanılıyor. Sprint 2 boyunca board'un ilerleyişi şöyle oldu:

**Erken Sprint 2 durumu:** Organizasyonel kararların çoğu (proje konusu, roller) Done'a geçmişti; takım ismi ve ürün ismi henüz In Progress, veri seti araştırması To Do'daydı.

**Sprint 2 sonu (güncel) durumu:**
- **Done (3):** Takım ismi belirlendi (SCRUM-2), Ürün ismi belirlendi (SCRUM-3), Veri seti araştırıldı (SCRUM-8)
- **In Progress (3):** Canlı Kamera Akışı UI (SCRUM-9), Sağ panel UI bileşenleri (SCRUM-10), Backend API kurulumu (SCRUM-11)
- **To Do (2):** YOLOv11 model entegrasyonu (SCRUM-12), RAG bilgi tabanı hazırlığı (SCRUM-13)

![Sprint Board](#)

### Ürün Durumu
> Canlı kamera akışı arayüzü ve backend API kurulumu **devam ediyor**. YOLOv11 modelinin backend'e entegrasyonu ve RAG bilgi tabanı hazırlığı **henüz başlamadı**. Bu noktada çalışan bir uçtan uca demo bulunmamaktadır.

<img width="1600" height="900" alt="image" src="https://github.com/user-attachments/assets/d327b283-775a-4ecd-8b42-8324e5f1eb8f" />


![Ürün Durumu](#)

### Sprint Review
- **Tamamlanan:**
  - Takım ismi belirlendi (SCRUM-2)
  - Ürün ismi belirlendi (SCRUM-3)
  - Veri seti araştırması tamamlandı (SCRUM-8)
- **Tamamlanamayan:**
  - Canlı Kamera Akışı (Live Stream) ekranı ve üzerine gelecek bounding box (tespit kutuları) bileşeninin arayüz kodlaması — devam ediyor (SCRUM-9)
  - Sağ paneldeki "Oturum İstatistikleri", "Güven Eşiği Slider'ı" ve "Son Taramalar" tablosunun UI bileşenleri — devam ediyor (SCRUM-10)
  - Backend API projesinin kurulması ve konfigürasyon ayarları — devam ediyor (SCRUM-11)
  - RAG sisteminde kullanılacak bilgi tabanının (aflatoksin regülasyonları, incir kalite kriterleri, tarım mevzuatları) toplanması ve metin temizleme işlemleri — başlanmadı (SCRUM-13)
  - YOLOv11 modelinin backend servisine entegre edilmesi ve ilk çıkarım (inference) testleri — başlanmadı (SCRUM-12)
- **Neden:** Sprint'in ilk bölümü takım ismi/ürün ismi/rol dağılımı gibi organizasyonel kararlara ve veri seti araştırmasına ayrıldı. Bu nedenle asıl teknik geliştirmeye (arayüz, backend, model entegrasyonu) görece geç başlandı; UI ve backend kurulumu paralel yürütülse de YOLOv11 entegrasyonu ve RAG bilgi tabanı hazırlığı sprint sonuna yetiştirilemedi.

### Sprint Retrospective

| İyi Gitti | İyileştirilmeli | Aksiyon |
|---|---|---|
| Organizasyonel kararlar (takım ismi, ürün ismi, veri seti araştırması) net şekilde tamamlandı | Teknik geliştirmeye geçiş beklenenden geç oldu | Sprint 3'te sprint'in ilk gününden itibaren doğrudan teknik backlog'a odaklanılacak |
| UI (canlı kamera akışı, sağ panel) ve backend API kurulumu paralel şekilde ilerletildi | YOLOv11 model entegrasyonu ve RAG bilgi tabanı hazırlığı henüz başlamadı | Sprint 3'te bu iki iş önceliklendirilecek, gerekirse ek kaynak ayrılacak |
| Jira board üzerinde ilerleme düzenli takip edildi | Backend/model tarafı arayüz tarafına göre geride kaldı | Sprint 3'te backend/model çalışmasına ağırlık verilecek, UI ince ayarları sona bırakılacak |

---

## 📝 Sprint 3 İçin Öneriler (Scrum Master Notu)

1. **YOLOv11 ve RAG entegrasyonunu önceliklendirin:** Bunlar ürünün AI çekirdeği — final değerlendirmede en yüksek ağırlığı taşıyan kısım, Sprint 3'ün ilk günlerinde başlanmalı.
2. **MVP kapsamını netleştirin:** Final teslimde eksiksiz bir sistem yerine, çalışan uçtan uca bir demo (kamera → tespit → rapor) önceliklendirilmeli.
3. **GitHub kayıtlarını güncel tutun:** Her sprint kanıtı repo'da olmalı; Jira board'undaki ilerleme düzenli olarak README'ye yansıtılmalı.
4. **Video ve form hazırlığını erkenden planlayın:** 2 Ağustos'a kadar 3 dakikalık YouTube videosu + teslim formu gerekiyor, son güne bırakılmamalı.


## 🔗 Faydalı Linkler

- Örnek Repo: https://github.com/YapayZekaveTeknolojiAkademisi/BootcampScrumTemplate/tree/main
- Slack Kanalı: #bootcamp-2026
