# RAG Entegrasyon Notları

## Backend akışı

`app/services/rag_service.py` ağır modelleri uygulama açılırken değil, ilk RAG isteğinde yükler. Arama sırası:

1. Chroma + çok dilli Hugging Face embedding ile semantik benzerlik araması
2. Semantik bağımlılıklar kullanılamıyorsa Chroma SQLite metadata içindeki belge parçalarını anahtar kelime örtüşmesiyle sıralama
3. Gemini anahtarı varsa yalnızca getirilen kaynak bağlamıyla üretilmiş Türkçe yanıt
4. Gemini yoksa top kaynak parçalarından extractive yanıt

Bu tasarım görüntü işleme servisinin RAG paketlerinden bağımsız olarak açılmasını sağlar.

## Güvenlik

- RAG uçları access token gerektirir.
- Kullanıcı sorusu uzunluk sınırına tabidir.
- Gemini sistem talimatı kaynak dışı bilgi üretmemeyi zorunlu tutar.
- Gizli anahtarlar repoya veya frontend'e yazılmaz.
- Sağlanan `.env` dosyasındaki anahtar taşınmamıştır.

## Frontend akışı

WebSocket'ten `inspection` mesajı geldiğinde frontend aşağıdaki isteği yapar:

```http
POST /api/v1/rag/inspection-advice
Authorization: Bearer <access-token>
Content-Type: application/json

{
  "decision": "Aflatoxin",
  "confidence": 0.91
}
```

Dönen `answer` öneri panelinde, `sources` ise kaynak listesinde gösterilir.
