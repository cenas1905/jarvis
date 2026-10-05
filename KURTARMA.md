# JARVIS yedegini geri yukleme (Windows)

Bu GitHub deposu JARVIS'in kaynak kodu ve calistirma icin gereken paylasilabilir
dosyalarin yedegidir. Depo herkese acik oldugu icin API anahtarlari, telefon
rehberi, kisisel hafiza, gorusme veritabani, kamera/telefon ayarlari, gunlukler ve
uretilen arastirma/icerik dosyalari bu yedekte bulunmaz.

## Yeni bilgisayarda kurulum

1. Python 3.11 veya ustunu ve Git'i kur.
2. PowerShell'de:

   ```powershell
   git clone https://github.com/cenas1905/jarvis.git
   cd jarvis
   py -3.11 -m venv .venv311
   .\.venv311\Scripts\python.exe -m pip install --upgrade pip
   .\.venv311\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. `config\api_keys.example.json` dosyasini `config\api_keys.json` olarak kopyala
   ve kendi servis anahtarlarini JARVIS icinden veya yerel dosyadan ekle. Gercek
   anahtarlari GitHub'a, ekran goruntusune veya sohbet mesajina koyma.
4. `\.venv311\Scripts\python.exe main.py` ile baslat.

Mikrofon, hoparlor, telefon baglantisi, API anahtarlari ve uygulama izinleri yeni
bilgisayarda yeniden yapilandirilmalidir. Kisisel JARVIS hafizasi lazimsa onu
GitHub disinda sifreli bir yedekte sakla; bu depoda yoktur.

## Bu bilgisayardan degisiklik yedekleme

JARVIS kaynak klasorunde:

```powershell
git add -A
git status --short
git commit -m "JARVIS backup"
git push
```

`git status` cikisinda `config/api_keys.json`, `.env`, `memory/`, `logs/`,
`research/reports/`, `work/` veya sertifika/anahtar dosyalari gorunurse commit
etmeden once durdur ve ne oldugunu kontrol et.
