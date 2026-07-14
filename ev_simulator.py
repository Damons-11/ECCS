"""
ev_simulator.py
═══════════════
Simulator EV (Electric Vehicle) untuk testing OCPP Charge Point terminal.

Cara kerja yang BENAR:
  EV Simulator ──► cp_bridge.do_*() ──► ChargePoint ──► CSMS
                       (sama seperti app.py)

EV Simulator meniru user/kendaraan yang:
  1. Mencolokkan kabel (status Preparing)
  2. Authorize RFID
  3. CP kirim StartTransaction ke CSMS → status Charging
  4. Loop kirim MeterValues (SoC naik, suhu realistis)
  5. Stop saat SoC target tercapai atau Ctrl+C

Jalankan di terminal terpisah saat app.py + central_system.py sudah berjalan:
  python ev_simulator.py
  python ev_simulator.py --connector 1 --soc-start 10 --soc-target 90
  python ev_simulator.py --mode manual
  python ev_simulator.py --mode script --scenario scenarios/fast_charge.json
  python ev_simulator.py --create-scenarios
"""

import argparse
import json
import logging
import random
import time
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Import bridge langsung — sama seperti app.py ─────────────────────────────
from cp_bridge import bridge, CPStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EV] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Default config ────────────────────────────────────────────────────────────
DEFAULT_ID_TAG           = "A56DEF4B"
DEFAULT_BATTERY_KWH      = 4.0
DEFAULT_MAX_POWER_W      = 7400.0
DEFAULT_VOLTAGE_V        = 230.0
DEFAULT_METER_INTERVAL_S = 20


# ══════════════════════════════════════════════════════════════════════════════
# EVState — simulasi fisik baterai
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EVState:
    id_tag:            str   = DEFAULT_ID_TAG
    connector_id:      int   = 1
    battery_kwh:       float = DEFAULT_BATTERY_KWH
    soc_pct:           float = 20.0
    soc_target:        float = 80.0
    max_power_w:       float = DEFAULT_MAX_POWER_W
    voltage_v:         float = DEFAULT_VOLTAGE_V
    temperature_c:     float = 25.0
    meter_interval_s:  int   = DEFAULT_METER_INTERVAL_S

    # Runtime
    energy_wh:         float = 0.0    # total energi di baterai (Wh)
    energy_delivered:  float = 0.0    # energi yg dikirim sesi ini (Wh)
    session_start:     Optional[datetime] = None

    def __post_init__(self):
        # Hitung energi awal dari SoC
        self.energy_wh = (self.soc_pct / 100.0) * self.battery_kwh * 1000

    @property
    def current_a(self) -> float:
        return self.effective_power / self.voltage_v if self.voltage_v else 0

    @property
    def effective_power(self) -> float:
        """CC-CV: taper daya di atas 80% SoC."""
        if self.soc_pct > 80:
            taper = 1.0 - ((self.soc_pct - 80) / 20.0) * 0.7
            return self.max_power_w * max(0.3, taper)
        return self.max_power_w

    @property
    def soc_reached(self) -> bool:
        return self.soc_pct >= self.soc_target

    def tick(self, seconds: float):
        """Simulasikan pengisian selama `seconds` detik, update semua nilai."""
        power_w   = self.effective_power
        delta_wh  = power_w * (seconds / 3600.0)
        total_wh  = self.battery_kwh * 1000

        self.energy_wh        += delta_wh
        self.energy_delivered += delta_wh
        self.soc_pct           = min(100.0, (self.energy_wh / total_wh) * 100)

        # Suhu naik saat mengisi, noise sensor kecil
        heat = (power_w / self.max_power_w) * 0.06
        self.temperature_c = min(55.0, self.temperature_c + heat - 0.01)
        self.temperature_c += random.uniform(-0.15, 0.15)

    def with_noise(self, value: float, pct: float = 0.02) -> float:
        return value * (1 + random.uniform(-pct, pct))


# ══════════════════════════════════════════════════════════════════════════════
# EVSimulator — mengontrol cp_bridge untuk meniru EV
# ══════════════════════════════════════════════════════════════════════════════

class EVSimulator:

    def __init__(self, ev: EVState):
        self.ev   = ev
        self._stop = False

    # ── Cek status bridge ─────────────────────────────────────────────────────

    def _wait_connected(self, timeout: int = 15) -> bool:
        """Tunggu bridge terhubung ke CSMS, maks timeout detik."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if bridge.is_connected:
                return True
            if bridge.status == CPStatus.ERROR:
                logger.error(f"Bridge error: {bridge.error}")
                return False
            time.sleep(0.5)
        logger.error("Timeout menunggu koneksi bridge")
        return False

    def _check_connector_free(self) -> bool:
        cid = self.ev.connector_id
        if bridge.active_txn(cid) is not None:
            logger.error(f"CON {cid} sudah ada transaksi aktif #{bridge.active_txn(cid)}")
            return False
        if bridge.connector_status(cid) not in ("Available", "Preparing"):
            logger.warning(f"CON {cid} status: {bridge.connector_status(cid)}")
        return True

    # ── Langkah-langkah sesi ──────────────────────────────────────────────────

    def step_plug_in(self):
        """EV mencolokkan kabel → status Preparing."""
        logger.info(f"🔌 EV plug-in ke CON {self.ev.connector_id}")
        bridge.do_status_notification(self.ev.connector_id, "Preparing")
        time.sleep(1.0)

    def step_authorize(self) -> bool:
        """Kirim Authorize dan tunggu respons (cek di log)."""
        logger.info(f"🪪 Authorize id_tag={self.ev.id_tag} …")
        bridge.do_authorize(self.ev.id_tag)
        time.sleep(1.5)   # beri waktu respons dari CSMS
        # Cek log terbaru untuk respons Authorize
        logs = bridge.logs
        for entry in reversed(logs[-10:]):
            if "Authorize" in entry.action and entry.direction == "in":
                try:
                    parsed = json.loads(entry.raw)
                    status = parsed[2].get("idTagInfo", {}).get("status", "")
                    if status == "Accepted":
                        logger.info("  → Accepted ✓")
                        return True
                    else:
                        logger.warning(f"  → {status} ✗")
                        return False
                except Exception:
                    pass
        logger.info("  → Authorize terkirim (tidak bisa baca respons)")
        return True   # asumsi diterima jika tidak bisa parse

    def step_start_transaction(self) -> bool:
        """CP kirim StartTransaction → tunggu txn_id muncul di bridge."""
        cid = self.ev.connector_id
        logger.info(f"▶ StartTransaction CON {cid} meter={self.ev.energy_wh:.0f}Wh …")
        bridge.do_start_transaction(cid, self.ev.id_tag, int(self.ev.energy_wh))
        self.ev.session_start = datetime.now()

        # Tunggu transaction_id muncul di bridge (maks 10 detik)
        deadline = time.time() + 10
        while time.time() < deadline:
            txn = bridge.active_txn(cid)
            if txn is not None:
                logger.info(f"  → transactionId={txn} ✓")
                return True
            time.sleep(0.5)

        # Fallback — mungkin txn_id tidak ter-parse, tapi status sudah Charging
        if bridge.connector_status(cid) == "Charging":
            logger.info("  → Charging (txn_id tidak terbaca, lanjut)")
            return True

        logger.warning("  → Timeout tunggu transactionId")
        return True   # tetap lanjut, biarkan user lihat di app

    def step_charging_loop(self):
        """Loop kirim MeterValues sampai SoC target atau di-stop."""
        cid      = self.ev.connector_id
        interval = self.ev.meter_interval_s

        logger.info(
            f"🔋 Charging loop — "
            f"SoC {self.ev.soc_pct:.1f}% → {self.ev.soc_target:.1f}% "
            f"(interval {interval}s)"
        )
        print()

        while not self._stop:
            time.sleep(interval)
            if self._stop:
                break

            # Simulasikan fisik baterai
            self.ev.tick(interval)

            # Kirim ke bridge (yang teruskan ke CSMS)
            bridge.do_meter_values(
                connector_id  = cid,
                energy_wh     = self.ev.with_noise(self.ev.energy_wh, 0.001),
                power_w       = self.ev.with_noise(self.ev.effective_power),
                voltage_v     = self.ev.with_noise(self.ev.voltage_v, 0.01),
                current_a     = self.ev.with_noise(self.ev.current_a),
                temperature_c = self.ev.with_noise(self.ev.temperature_c, 0.02),
                soc_pct       = round(self.ev.soc_pct, 1),
            )

            self._print_progress()

            if self.ev.soc_reached:
                print()
                logger.info(f"✅ SoC target {self.ev.soc_target:.0f}% tercapai!")
                break

        print()

    def step_stop_transaction(self):
        """CP kirim StopTransaction → status kembali Available."""
        cid = self.ev.connector_id
        logger.info(f"■ StopTransaction CON {cid} meter={self.ev.energy_wh:.0f}Wh …")
        bridge.do_stop_transaction(cid, self.ev.id_tag)
        time.sleep(1.0)
        logger.info("  → Done ✓")

    def step_unplug(self):
        """EV cabut kabel → status Available."""
        logger.info(f"🔌 EV unplug dari CON {self.ev.connector_id}")
        # Status sudah Available dari do_stop_transaction, cukup log
        self._print_summary()

    # ── Progress & summary ────────────────────────────────────────────────────

    def _print_progress(self):
        soc    = self.ev.soc_pct
        target = self.ev.soc_target
        filled = int((soc / 100) * 32)
        bar    = "█" * filled + "░" * (32 - filled)
        elapsed = ""
        if self.ev.session_start:
            s = int((datetime.now() - self.ev.session_start).total_seconds())
            elapsed = f"{s//60:02d}:{s%60:02d}"
        print(
            f"\r  [{bar}] {soc:5.1f}%→{target:.0f}%"
            f"  {self.ev.energy_delivered:7.1f}Wh"
            f"  {self.ev.effective_power/1000:.1f}kW"
            f"  {self.ev.temperature_c:.1f}°C"
            f"  {elapsed}  ",
            end="", flush=True
        )

    def _print_summary(self):
        if not self.ev.session_start:
            return
        dur = int((datetime.now() - self.ev.session_start).total_seconds())
        m, s = divmod(dur, 60)
        avg_power = (self.ev.energy_delivered / (dur / 3600)) if dur > 0 else 0
        print(f"""
╔══════════════════════════════════════════════╗
║              SESSION SUMMARY                 ║
╠══════════════════════════════════════════════╣
║  Duration    : {m:02d}:{s:02d}                         ║
║  Energy      : {self.ev.energy_delivered:8.1f} Wh                  ║
║  Avg Power   : {avg_power:8.1f} W                   ║
║  SoC final   : {self.ev.soc_pct:8.1f} %                   ║
║  Final Temp  : {self.ev.temperature_c:8.1f} °C                  ║
╚══════════════════════════════════════════════╝""")

    def stop(self):
        self._stop = True

    def run_in_thread(self) -> threading.Thread:
        """Jalankan run_auto() di background thread agar bisa dipanggil dari app.py."""
        t = threading.Thread(target=self.run_auto, daemon=True, name="EVSimulator")
        t.start()
        return t

    # ── Session runners —──────────────────────────────────────────────────────

    def run_auto(self):
        """Jalankan sesi penuh secara otomatis."""
        print(f"\n  [AUTO] CON {self.ev.connector_id} | "
              f"SoC {self.ev.soc_pct:.0f}% → {self.ev.soc_target:.0f}% | "
              f"{self.ev.max_power_w/1000:.1f}kW | "
              f"interval {self.ev.meter_interval_s}s\n")

        if not self._wait_connected():
            return
        if not self._check_connector_free():
            return

        try:
            self.step_plug_in()
            if not self.step_authorize():
                bridge.do_status_notification(self.ev.connector_id, "Available")
                return
            if not self.step_start_transaction():
                bridge.do_status_notification(self.ev.connector_id, "Available")
                return
            self.step_charging_loop()
        except KeyboardInterrupt:
            print("\n\n  ⚠ Dihentikan oleh user")
        finally:
            self.stop()
            self.step_stop_transaction()
            self.step_unplug()

    def run_manual(self):
        """Sesi dengan konfirmasi tiap langkah."""
        print("\n  [MANUAL] Tiap langkah perlu konfirmasi. Ketik 'q' untuk batal.\n")

        if not self._wait_connected():
            return

        def ask(msg: str) -> bool:
            ans = input(f"\n  {msg} [Enter / q]: ").strip().lower()
            return ans != "q"

        if not ask("1. Plug-in EV ke connector?"):
            return
        self.step_plug_in()

        if not ask("2. Authorize RFID?"):
            bridge.do_status_notification(self.ev.connector_id, "Available")
            return
        if not self.step_authorize():
            bridge.do_status_notification(self.ev.connector_id, "Available")
            return

        if not ask("3. Start Transaction?"):
            bridge.do_status_notification(self.ev.connector_id, "Available")
            return
        if not self.step_start_transaction():
            return

        print("\n  Charging dimulai. Tekan Ctrl+C untuk stop lebih awal.\n")
        try:
            self.step_charging_loop()
        except KeyboardInterrupt:
            print("\n\n  ⚠ Dihentikan oleh user")

        self.stop()

        if not ask("4. Stop Transaction?"):
            logger.info("Stop dibatalkan — transaksi tetap berjalan di CP")
            return

        self.step_stop_transaction()
        self.step_unplug()


# ── Module-level tracker — dipakai app.py —───────────────────────────────────
_active_simulators = {}


def start_ev_sim(
    connector_id:       int   = 1,
    id_tag:             str   = DEFAULT_ID_TAG,
    soc_start:          float = 20.0,
    soc_target:         float = 80.0,
    battery_kwh:        float = DEFAULT_BATTERY_KWH,
    max_power_w:        float = DEFAULT_MAX_POWER_W,
    meter_interval_s:   int   = DEFAULT_METER_INTERVAL_S,
) -> EVSimulator:
    """Buat dan jalankan EV simulator baru di background thread untuk connector tertentu."""
    global _active_simulators
    
    # Jika konektor ini sudah punya simulator aktif, hentikan dulu
    if connector_id in _active_simulators and not _active_simulators[connector_id]._stop:
        _active_simulators[connector_id].stop()

    ev = EVState(
        id_tag           = id_tag,
        connector_id     = connector_id,
        soc_pct          = soc_start,
        soc_target       = soc_target,
        battery_kwh      = battery_kwh,
        max_power_w      = max_power_w,
        meter_interval_s = meter_interval_s,
    )
    sim = EVSimulator(ev)
    _active_simulators[connector_id] = sim
    sim.run_in_thread()
    return sim


def stop_ev_sim():
    """Hentikan SEMUA simulator yang berjalan."""
    global _active_simulators
    for cid in list(_active_simulators.keys()):
        _active_simulators[cid].stop()
    _active_simulators.clear()


def get_active_sim() -> Optional[EVSimulator]:
    return _active_sim


# ══════════════════════════════════════════════════════════════════════════════
# Scenario runner
# ══════════════════════════════════════════════════════════════════════════════

def run_scenario(path: str):
    p = Path(path)
    if not p.exists():
        logger.error(f"File tidak ditemukan: {path}")
        logger.info("Tip: jalankan --create-scenarios untuk membuat contoh")
        return

    with open(p) as f:
        scenario = json.load(f)

    logger.info(f"🎬 Skenario: {scenario.get('name', path)}")
    sessions = scenario.get("sessions", [])

    for i, sess in enumerate(sessions, 1):
        logger.info(f"\n── Session {i}/{len(sessions)} ──")
        ev = EVState(
            id_tag           = sess.get("id_tag",            DEFAULT_ID_TAG),
            connector_id     = sess.get("connector_id",      1),
            soc_pct          = sess.get("soc_start",         20.0),
            soc_target       = sess.get("soc_target",        80.0),
            battery_kwh      = sess.get("battery_capacity_kwh", DEFAULT_BATTERY_KWH),
            max_power_w      = sess.get("charge_power_w",    DEFAULT_MAX_POWER_W),
            meter_interval_s = sess.get("meter_interval_s",  DEFAULT_METER_INTERVAL_S),
        )
        sim = EVSimulator(ev)
        try:
            sim.run_auto()
        except KeyboardInterrupt:
            logger.info("Skenario dihentikan")
            return

        if i < len(sessions):
            delay = sess.get("next_session_delay_s", 5)
            logger.info(f"  Jeda {delay}s …")
            time.sleep(delay)


def create_default_scenarios():
    Path("scenarios").mkdir(exist_ok=True)
    files = {
        "normal_charge.json": {
            "name": "Normal AC Charge",
            "sessions": [{
                "connector_id": 1, "id_tag": "A56DEF4B",
                "soc_start": 20, "soc_target": 80,
                "battery_capacity_kwh": 60, "charge_power_w": 7400,
                "meter_interval_s": 10
            }]
        },
        "fast_charge.json": {
            "name": "Fast Charge DC",
            "sessions": [{
                "connector_id": 1, "id_tag": "RFID_FAST",
                "soc_start": 10, "soc_target": 80,
                "battery_capacity_kwh": 75, "charge_power_w": 50000,
                "meter_interval_s": 5
            }]
        },
        "multi_session.json": {
            "name": "Multi Session Test",
            "sessions": [
                {
                    "connector_id": 1, "id_tag": "EV_USER_1",
                    "soc_start": 30, "soc_target": 60,
                    "battery_capacity_kwh": 40, "charge_power_w": 7400,
                    "meter_interval_s": 8, "next_session_delay_s": 5
                },
                {
                    "connector_id": 1, "id_tag": "EV_USER_2",
                    "soc_start": 5, "soc_target": 50,
                    "battery_capacity_kwh": 60, "charge_power_w": 7400,
                    "meter_interval_s": 8
                }
            ]
        },
    }
    for name, data in files.items():
        dest = Path("scenarios") / name
        with open(dest, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"  Dibuat: {dest}")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        description="EV Simulator — test OCPP Charge Point via cp_bridge",
        epilog="""
Contoh:
  python ev_simulator.py
  python ev_simulator.py --connector 2 --soc-start 5 --soc-target 95
  python ev_simulator.py --mode manual
  python ev_simulator.py --mode script --scenario scenarios/fast_charge.json
  python ev_simulator.py --create-scenarios
        """
    )
    p.add_argument("--mode",    default="auto", choices=["auto","manual","script"])
    p.add_argument("--scenario", default=None,  help="Path JSON skenario (mode script)")
    p.add_argument("--connector",   type=int,   default=1,                help="Connector ID")
    p.add_argument("--id-tag",      default=DEFAULT_ID_TAG,               help="RFID / ID tag")
    p.add_argument("--soc-start",   type=float, default=20.0,             help="SoC awal (%%)")
    p.add_argument("--soc-target",  type=float, default=80.0,             help="SoC target (%%)")
    p.add_argument("--battery-kwh", type=float, default=DEFAULT_BATTERY_KWH)
    p.add_argument("--power-w",     type=float, default=DEFAULT_MAX_POWER_W)
    p.add_argument("--interval",    type=int,   default=DEFAULT_METER_INTERVAL_S, help="Detik antar MeterValues")
    p.add_argument("--create-scenarios", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()

    if args.create_scenarios:
        create_default_scenarios()
        return

    print(f"""
╔══════════════════════════════════════════════════════╗
║           EV SIMULATOR — OCPP 1.6                   ║
╠══════════════════════════════════════════════════════╣
║  Mode      : {args.mode.upper():<38} ║
║  CP Bridge : {("Connected" if bridge.is_connected else bridge.status.value):<38} ║
║  Connector : CON {args.connector:<35} ║
║  ID Tag    : {args.id_tag:<38} ║
║  Battery   : {args.battery_kwh:.0f} kWh{'':<33} ║
║  SoC       : {args.soc_start:.0f}% → {args.soc_target:.0f}%{'':<32} ║
║  Power     : {args.power_w:.0f} W{'':<35} ║
║  Interval  : {args.interval}s MeterValues{'':<28} ║
╚══════════════════════════════════════════════════════╝""")

    if args.mode == "script":
        if not args.scenario:
            print("\n  Error: --scenario wajib untuk mode script")
            return
        run_scenario(args.scenario)
        return

    ev = EVState(
        id_tag           = args.id_tag,
        connector_id     = args.connector,
        soc_pct          = args.soc_start,
        soc_target       = args.soc_target,
        battery_kwh      = args.battery_kwh,
        max_power_w      = args.power_w,
        meter_interval_s = args.interval,
    )
    sim = EVSimulator(ev)
    try:
        if args.mode == "manual":
            sim.run_manual()
        else:
            sim.run_auto()
    except KeyboardInterrupt:
        print("\n\nDihentikan.")
        sim.stop()

def stop_ev_sim_by_id(connector_id: int):
    """Hentikan simulator pada konektor tertentu."""
    global _active_simulators
    if connector_id in _active_simulators:
        _active_simulators[connector_id].stop()
        del _active_simulators[connector_id]
        print(f"[SIMULATOR] Konektor {connector_id} dihentikan.")        


if __name__ == "__main__":
    main()
