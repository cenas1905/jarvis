# JARVIS robot kol — ilk güvenli test

Kod: [robot_arm.ino](robot_arm.ino)

1. Kolu mekanik olarak destekle. İlk servo hareketi mevcut açı bilinmediği için sıçrayabilir. `release` veya ESP32 reseti servo tutmasını keser; kol düşebilir. İlk yüklemede motorların harici 5 V beslemesini kapat, ESP32'yi yalnız USB ile bilgisayara bağla.
2. Arduino IDE'de **File > Open** ile `robot_arm.ino` dosyasını aç. ESP32 kartları yoksa **File > Preferences > Additional Boards Manager URLs** içine `https://espressif.github.io/arduino-esp32/package_esp32_index.json` ekle; **Tools > Board > Boards Manager** içinden **esp32 by Espressif Systems** kur. **Sketch > Include Library > Manage Libraries** içinden **ESP32Servo** kur. Güncel ESP32Servo, Arduino-ESP32 3.x ile uyumludur.
3. **Tools > Board > ESP32 Arduino > ESP32 Dev Module** seç. **Tools > Port** bölümünde kartın COM portunu seç. Bu bilgisayarda şu anda bir **CP210x / COM5** görünüyor; kartın bu olduğunu USB çıkar-tak ile doğrula.
4. IDE'de **Verify** (✓) ile derle, ardından **Upload** (→) ile yükle. "Connecting..." takılırsa kartın **BOOT** tuşunu bağlantı başlayana kadar basılı tutup bırak.
5. Yükleme bitince 5 V motor beslemesini aç. Arduino **Serial Monitor**: **115200 baud**, satır sonu **Newline**. Sırayla `help`, `status`, `step 8`, `step -8` dene. Servo için kolu destekle ve önce `servo 5`, sonra `jog 1` dene. `servo 5` tek başına PWM başlatmaz; ilk `jog` başlatır.
6. Testte `stop` hareketi durdurur ama servo son açısını tutar. `release` ve `!` servo PWM'ini ve step bobinlerini kapatır. Kolu desteklemeden `release` kullanma. Step motorda limit/homing yoktur; çok sayıda komutu peş peşe verip mekanik sınıra bindirme.
7. JARVIS'e geçerken **Serial Monitor'ü kapat** (COM portu aynı anda iki program açamaz). JARVIS'i yeniden başlat. "Robot kolun durumunu söyle" de; ardından yalnız test için "Kıskacı 1 jog hareket ettir" veya "Step motoru 8 yarım adım çevir" de. JARVIS otomatik USB-seri portu bulur; birden fazla kart varsa `app_config.py` içindeki `robot_arm_port` ayarına doğru COM portunu yaz.

**Pinler:** Servo 1→GPIO13, 2→GPIO14, 3→GPIO25, 4→GPIO26, 5→GPIO27. ULN2003 IN1→GPIO18, IN2→GPIO19, IN3→GPIO21, IN4→GPIO22. ULN2003 ve servolar regüle harici 5 V'tan; bütün GND'ler ortak. Motor gücünü ESP32 5 V pininden veya MB102/HW-131'den alma.

Bu kod yalnız küçük seri test komutları içindir. Fiziksel limit anahtarı, yük/akım koruması ve homing olmadan tam otomatik robot kol hareketleri güvenli değildir.
