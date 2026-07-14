# ECCS - Electric Charging Client System ⚡

ECCS adalah aplikasi *Charge Point Client* berbasis Python yang dirancang untuk mendukung riset e-Mobility di Pusat Riset Teknologi Kelistrikan BRIN. Aplikasi ini mensimulasikan operasional fisik stasiun pengisian kendaraan listrik (EVSE) dan berkomunikasi dengan CSMS menggunakan standar **OCPP 1.6 JSON** via WebSocket.

## 📂 Struktur Proyek & Modul

Arsitektur aplikasi ini dibagi menjadi beberapa modul independen agar mudah dipelihara dan dikembangkan:

- **`main.py`** 
  Orkestrator utama. Bertugas memuat antarmuka pengguna (`CustomTkinter`), membaca konfigurasi, dan menjembatani *thread* UI dengan *async event loop* jaringan.
- **`charge_point.py`**
  Inti dari logika komunikasi. Memuat kelas `ChargePoint` yang menangani pembentukan pesan (*payload*), siklus transaksi (Authorize, Start/Stop), pengiriman *Heartbeat*, serta merespons perintah *Remote* dari CSMS.
- **`ev_simulator.py`**
  Mesin simulasi kelistrikan. Bertugas melakukan kalkulasi matematis *real-time* untuk parameter fisik seperti Tegangan (V), Arus (A), Daya (kW), akumulasi Energi (kWh), dan *State of Charge* (SoC) tanpa memerlukan perangkat keras.
- **`config.json`**
  File konfigurasi sentral yang menyimpan kredensial stasiun (ID Charger) dan *endpoint* WebSocket CSMS.
- **`requirements.txt`**
  Daftar dependensi *library* Python yang dibutuhkan untuk menjalankan aplikasi.

## 🌟 Fitur Utama
- **Isolasi Multi-Konektor:** Mendukung pengoperasian beberapa konektor secara bersamaan di memori terpisah untuk menghindari *race condition*.
- **Dynamic Meter Values:** Mengirimkan data kelistrikan yang fluktuatif dan realistis ke CSMS.
- **Session Security:** Dilengkapi fitur *Screen Lock* untuk menahan sesi di fase *Finishing* hingga pengguna secara sadar mencabut konektor.
- **Asynchronous Design:** Memastikan koneksi WebSocket tetap hidup (`Ping/Pong`) meskipun UI sedang memproses *input* pengguna.

## ⚙️ Persyaratan Sistem
- **OS:** Windows / Linux / macOS
- **Python:** Versi 3.9 atau lebih baru
- Jaringan yang dapat menjangkau *server* CSMS

## 🚀 Cara Menjalankan

1. **Clone repository ini:**
   ```bash
   git clone [https://github.com/Damons-11/ECCS.git](https://github.com/Damons-11/ECCS.git)
   cd ECCS
2. pip install -r requirements.txt
3. python main.py  
