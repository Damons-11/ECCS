# ECCS - Electric Charging Client System ⚡

ECCS adalah aplikasi *Charge Point Client* berbasis Python yang dirancang untuk mendukung riset e-Mobility di Pusat Riset Teknologi Kelistrikan BRIN. Aplikasi ini mensimulasikan operasional fisik stasiun pengisian kendaraan listrik (EVSE) dan berkomunikasi dengan CSMS menggunakan standar **OCPP 1.6 JSON** via WebSocket.

## 📂 Struktur Proyek & Modul

Arsitektur aplikasi ini dibagi menjadi beberapa modul independen agar mudah dipelihara dan dikembangkan:ocpp_chargepoint/
#Arsitektur file
├── app.py                  # UI Streamlit utama (dashboard)
├── charge_point.py         # Model OCPP ChargePoint — semua handler & send
├── cp_bridge.py            # Jembatan async↔sync, state management, log
├── event_loop.py           # Shared persistent asyncio event loop
├── shared_cp.py            # Thread-safe WebSocket reference
├── ev_simulator.py         # Simulator kendaraan listrik
├── central_system.py       # CSMS lokal untuk testing
├── requirements.txt        # Dependencies Python
├── DESIGN.md               # Spesifikasi desain UI (untuk Stitch/AI tools)
│
├── assets/                 # File media (buat folder ini jika belum ada)
│   ├── nfc.gif    # Animasi karakter di atas SOC bar
│   ├── data-charger.png            # Logo BRIN (sidebar)
│   ├── mobil-biru.png      # Ilustrasi kendaraan (layar idle)
│   └── EV-cable.png  # cable disamping mobil biru
│   └── brin.png            # Logo BRIN 

## 🌟 Fitur Utama
- **Isolasi Multi-Konektor:** Mendukung pengoperasian beberapa konektor secara bersamaan di memori terpisah untuk menghindari *race condition*.
- **Dynamic Meter Values:** Mengirimkan data kelistrikan yang fluktuatif dan realistis ke CSMS.
- **Session Security:** Dilengkapi fitur *Screen Lock* untuk menahan sesi di fase *Finishing* hingga pengguna secara sadar mencabut konektor.
- **Asynchronous Design:** Memastikan koneksi WebSocket tetap hidup (`Ping/Pong`) meskipun UI sedang memproses *input* pengguna.

## ✅ Fitur lainnya

### OCPP 1.6 Core
| Fitur | Status |
|---|---|
| BootNotification | ✅ |
| Heartbeat (otomatis periodik) | ✅ |
| Authorize (RFID) | ✅ |
| StartTransaction | ✅ |
| StopTransaction | ✅ |
| MeterValues (Energy, Power, Voltage, Current, Temperature, SoC) | ✅ |
| StatusNotification (semua status OCPP) | ✅ |
| RemoteStartTransaction | ✅ |
| RemoteStopTransaction | ✅ |
| ChangeAvailability | ✅ |
| Reset (Hard/Soft) | ✅ |
| GetConfiguration / ChangeConfiguration | ✅ |
| TriggerMessage | ✅ |
| UnlockConnector | ✅ |

## ⚙️ Persyaratan Sistem
- **OS:** Windows / Linux / macOS
| Komponen | Versi Minimum |
|---|---|
| Python | 3.10+ |
| Streamlit | 1.32.0+ |
| websockets | 12.0+ |
| ocpp | 0.26.0+ |
| pandas | (untuk grafik) |

## 🚀 Cara Menjalankan

1. **Clone repository ini:**
   ```bash
   git clone [https://github.com/Damons-11/ECCS.git](https://github.com/Damons-11/ECCS.git)
   cd ECCS
2. pip install -r requirements.txt
3. python main.py
4. streamlit run main-alt.py

## ⚙️ Konfigurasi

Semua konfigurasi utama ada di **`charge_point.py`** bagian atas file:

```python
# ── Konfigurasi Charge Point ──────────────────────────
CSMS_URL            = "ws://127.0.0.1:9000"   # URL WebSocket CSMS
CHARGE_POINT_ID     = "CP_001"                # ID unik charge point ini
CHARGE_POINT_MODEL  = "SmartCharger-7kW"      # Model perangkat
CHARGE_POINT_VENDOR = "IndonesiaEV"           # Nama vendor
ID_TAG              = "A56DEF4B"              # RFID default
CONNECTOR_ID        = 1                        # Connector default
NUMBER_OF_CONNECTORS = 2                       # Jumlah connector

> **Catatan:** Ubah `CSMS_URL` sesuai alamat server CSMS yang digunakan.  
> Server lokal: `ws://127.0.0.1:9000`  
> Server jaringan: `ws://192.168.1.x:9000`  
> Server publik: `ws://31.97.62.249:9090/csmsbrin`

