# AgroVizyon [Takım ismi kesinleşince güncellenecek]

> Yapay Zeka ve Teknoloji Akademisi — Bootcamp 2026, 5. Akademi Dönemi

---

## 👥 Takım Bilgileri

| İsim                 | Rol           |
| -------------------- | ------------- |
| İrem Ayça Uçankale   | Product Owner |
| Enver Buğrahan Alyüz | Scrum Master  |
| Esma Tuğçe Kireşci   | Developer     |
| Rukiye Dişçi         | Developer     |
| fatihyalcimin        | Developer     |

---

## 🌱 Ürün İle İlgili Bilgiler

**Ürün İsmi:** Belirlenecek (aday alan: Tarım + Yapay Zeka)

**Ürün Açıklaması:**

> Tarım alanında küçük/orta ölçekli üreticilere yönelik yapay zeka destekli bir karar destek çözümü geliştirilmesi planlanmaktadır. Sprint 1 kapsamında spesifik problem tanımı netleştirilmemiş olup, aday alanlar araştırılmıştır.

**Ürün Özellikleri:**

- [Sprint 2'de netleştirilecek — aday özellik listesi aşağıda "Sprint Review" bölümünde]

**Hedef Kitle:**

- Küçük/orta ölçekli çiftçiler
- Tarım kooperatifleri
- (Netleşecek fikre göre daraltılacak)

**Product Backlog URL:** [Sprint 2'de oluşturulacak — Notion linki eklenecek]

---

## 🏃 Sprint 1 (19 Haziran – 5 Temmuz 2026)

### Backlog Dağıtma Mantığı

> Bu sprint'te henüz teknik geliştirme backlog'u oluşturulmadı. Sprint 1, **proje scope'unu belirleme ve sektör araştırması** sprint'i olarak planlandı. Yapılan araştırma görevleri Bucket System ile kabaca gruplandırıldı (tartışmadan, göz kararıyla):

- [x] Kova (Küçük efor): Tarım+AI alanında genel literatür/pazar taraması yapmak
- [x] Kova (Orta efor): Aday problem alanlarını listelemek ve kısa artı/eksi analizi çıkarmak
- [ ] Kova (Büyük efor): Aday alanlardan birini seçip hedef kitle + kaynak (veri seti, API vb.) uygunluğunu detaylı incelemek
- [ ] Kova (Orta efor): Seçilen fikir için ilk teknik backlog'u oluşturmak (Sprint 2'ye devretti)

**Aday problem alanları (araştırma sonucu):**

1. Bitki hastalığı/zararlı teşhisi (görsel veri ile CNN tabanlı tespit)
2. Ürün verim/fiyat tahmini (zaman serisi + piyasa verisi)
3. Sulama/gübreleme öneri asistanı (sensör verisi + LLM agent)

### Daily Scrum Notları

> Sprint 1 boyunca ekip henüz düzenli daily formatına geçmediği için notlar sınırlıdır. Sprint 2'den itibaren her gün kısa yazılı check-in (Slack üzerinden) planlanmaktadır.

- 19 Haziran: Takım tanışma, bootcamp kılavuzu ortak okuma
- 25 Haziran: Aday problem alanları üzerine kısa değerlendirme
- 2 Temmuz: Sprint 1 kapanış, Sprint 2 planlaması için ön görüşme

### Sprint Board Updates

> Sprint 1'de resmi bir board henüz kurulmadı. Sprint 2 açılışında Notion üzerinde board oluşturulup bu bölüme ekran görüntüsü eklenecektir.

![Sprint Board](#)

### Ürün Durumu

> Bu aşamada geliştirilen bir ürün/prototip bulunmamaktadır. Sprint 1 çıktısı, üç aday problem alanının kısa değerlendirmesidir (yukarıdaki "Backlog Dağıtma Mantığı" bölümüne bakınız).

![Ürün Durumu](#)

### Sprint Review

- **Tamamlanan:** Tarım+AI alanında genel araştırma yapıldı, 3 aday problem alanı belirlendi, GitHub repo kurulumu yapıldı.
- **Tamamlanamayan:** Tek bir fikre karar verilemedi, teknik backlog oluşturulamadı.
- **Neden:** Takım üyelerinin bootcamp'in ilk haftasında yoğunlukları farklıydı (ders/staj/kişisel programlar), ilk toplanma ve senkronizasyon beklenenden geç oldu.

### Sprint Retrospective

| İyi Gitti                                               | İyileştirilmeli                                        | Aksiyon                                                                           |
| ------------------------------------------------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------- |
| Takım hızlıca 3 makul aday fikir üzerinde hemfikir oldu | Karar alma süreci çok yavaş ilerledi, fikir netleşmedi | Sprint 2'nin ilk 2 günü içinde fikir kesinleştirilecek (oylama ile)               |
| İletişim kanalı (Slack) sorunsuz kuruldu                | Düzenli daily/checkpoint alışkanlığı oluşmadı          | Sprint 2'de sabit saatli kısa daily check-in başlatılacak                         |
| —                                                       | GitHub repo ve Notion board kurulumu gecikti           | Sprint 2'nin ilk günü repo + Notion board kurulacak (Scrum Master sorumluluğunda) |

---

## 📝 Sprint 2 İçin Öneriler (Scrum Master Notu)

1. **Fikri hızlı kilitleyin:** 3 aday arasından seçim yaparken uzun tartışmaya girmeyin — kısa bir oylama (örn. her üye 1 oy) ile Sprint 2'nin ilk gününde karara bağlayın.
2. **Aynı gün repo + board kurulumu:** Fikir netleşir netleşmez GitHub repo public yapılsın, backlog board'u Notion üzerinde açılsın — bu ikisi olmadan sprint puanlaması için kanıt oluşmuyor.
3. **Fibonacci ile ilk gerçek tahminleme:** Sprint 2 planlamasında backlog'a giren story'ler için Fibonacci (1,2,3,5,8,13) kullanılması, önceki konuşmamızda değindiğimiz gibi hem standart hem de belgelemesi kolay bir yöntem.
4. **Daily'yi hafifletin:** Herkesin farklı yoğunluğu olduğu göz önüne alınırsa, sesli daily yerine Slack'te 3 satırlık yazılı check-in (dün ne yaptım / bugün ne yapacağım / blocker var mı) daha sürdürülebilir olur.
5. **Sprint 1'in eksikliğini telafi planı:** Sprint 2 sonunda değerlendirilecek "Backlog Dağıtma Mantığı" ve "Ürün Durumu" bölümlerinin gerçek ve dolu olmasına özellikle dikkat edin, çünkü Sprint 1 zayıf geçti — bu, proje yönetimi puanınızda telafi noktası olacak.

---

## 🔗 Faydalı Linkler

- Örnek Repo: https://github.com/YapayZekaveTeknolojiAkademisi/BootcampScrumTemplate/tree/main
- Slack Kanalı: #bootcamp-2026
