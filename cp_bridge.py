"""
cp_bridge.py - Multi-connector, auto TxnID, notification queue
"""

import asyncio, threading, logging, json, os, atexit, signal
from dataclasses import dataclass, field
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Deque
from enum import Enum

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from event_loop import get_loop
from shared_cp import shared_cp

logger = logging.getLogger(__name__)


class CPStatus(str, Enum):
    DISCONNECTED = "Disconnected"
    CONNECTING   = "Connecting"
    CONNECTED    = "Connected"
    ERROR        = "Error"


class LogEntry:
    def __init__(self, direction: str, action: str, raw: str,
                 timestamp: datetime = None):
        self.direction = direction
        self.action    = action
        self.raw       = raw
        self.timestamp = timestamp or datetime.now()

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "action":    self.action,
            "raw":       self.raw,
            "timestamp": self.timestamp.isoformat(),
        }

    @staticmethod
    def from_dict(d: dict) -> "LogEntry":
        return LogEntry(
            direction = d["direction"],
            action    = d["action"],
            raw       = d["raw"],
            timestamp = datetime.fromisoformat(d["timestamp"]),
        )


class RemoteNotification:
    """Notifikasi perintah remote dari CSMS."""
    ICONS = {
        "remote_start": "⚡",
        "remote_stop":  "🛑",
        "change_avail": "🔄",
        "reset":        "🔁",
    }
    LABELS = {
        "remote_start": "Remote Start",
        "remote_stop":  "Remote Stop",
        "change_avail": "Change Availability",
        "reset":        "Reset",
    }

    def __init__(self, kind: str, payload: dict):
        self.kind      = kind
        self.payload   = payload
        self.timestamp = datetime.now()
        self.read      = False

    @property
    def icon(self):  return self.ICONS.get(self.kind, "📡")
    @property
    def label(self): return self.LABELS.get(self.kind, self.kind)

    def summary(self) -> str:
        p = self.payload
        if self.kind == "remote_start":
            return f"Connector {p.get('connector_id')} — tag {p.get('id_tag')}"
        if self.kind == "remote_stop":
            return f"Transaction {p.get('transaction_id')}"
        if self.kind == "change_avail":
            return f"Connector {p.get('connector_id')} → {p.get('status')}"
        if self.kind == "reset":
            return f"Type: {p.get('type')}"
        return str(p)


class ConnectorBridgeState:
    def __init__(self, cid: int):
        self.cid            = cid
        self.status         = "Available"
        self.transaction_id: Optional[int] = None
        self.id_tag:         Optional[str] = None
        self.meter_wh:       float = 0.0
        self.soc_pct:        float = 0.0
        self.temperature_c:  float = 0.0
        # Session tracking
        self.session_start:  Optional[datetime] = None
        self.soc_at_start:   float = 0.0
        self.max_temp_c:     float = 0.0
        self.chart_points:   list  = []

    @property
    def is_charging(self): return self.transaction_id is not None


# ── Log persistence ──────────────────────────────────────────────────────────

LOG_DIR           = Path(__file__).parent / "logs"
HISTORY_DIR       = Path(__file__).parent / "logs" / "riwayat"
LOG_RETENTION_DAYS = 7


@dataclass
class ChartPoint:
    """Satu titik data untuk grafik real-time."""
    ts:            str
    soc_pct:       float
    power_w:       float
    energy_wh:     float
    temperature_c: float

    def to_dict(self) -> dict:
        return {"ts": self.ts, "soc": self.soc_pct,
                "power": self.power_w, "energy": self.energy_wh,
                "temp": self.temperature_c}


@dataclass
class SessionRecord:
    """Riwayat satu sesi charging yang sudah selesai."""
    connector_id:   int
    transaction_id: int
    id_tag:         str
    start_time:     str
    end_time:       str
    duration_s:     int
    energy_wh:      float
    soc_start:      float
    soc_end:        float
    avg_power_w:    float
    max_temp_c:     float
    chart_points:   list = field(default_factory=list)  # list of ChartPoint dicts

    def to_dict(self) -> dict:
        return {
            "connector_id":   self.connector_id,
            "transaction_id": self.transaction_id,
            "id_tag":         self.id_tag,
            "start_time":     self.start_time,
            "end_time":       self.end_time,
            "duration_s":     self.duration_s,
            "energy_wh":      round(self.energy_wh, 2),
            "soc_start":      round(self.soc_start, 1),
            "soc_end":        round(self.soc_end, 1),
            "avg_power_w":    round(self.avg_power_w, 1),
            "max_temp_c":     round(self.max_temp_c, 1),
            "chart_points":   self.chart_points,
        }


def _log_file_for_date(date: datetime) -> Path:
    """Return path: logs/YYYY-MM-DD.json"""
    LOG_DIR.mkdir(exist_ok=True)
    return LOG_DIR / f"{date.strftime('%Y-%m-%d')}.json"


def _save_log_entry(entry: LogEntry) -> None:
    """Append one log entry to today's JSON file."""
    path = _log_file_for_date(entry.timestamp)
    try:
        records = []
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
        records.append(entry.to_dict())
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Log save failed: {e}")


def _load_logs_last_n_days(n: int = LOG_RETENTION_DAYS) -> List[LogEntry]:
    """Load all log entries from the past n days."""
    entries = []
    today   = datetime.now()
    for i in range(n):
        date = today - timedelta(days=i)
        path = _log_file_for_date(date)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    records = json.load(f)
                entries.extend(LogEntry.from_dict(r) for r in records)
            except Exception as e:
                logger.warning(f"Log load failed {path}: {e}")
    # Sort by timestamp ascending
    entries.sort(key=lambda e: e.timestamp)
    return entries


def _purge_old_logs() -> None:
    """Delete log files older than LOG_RETENTION_DAYS days."""
    if not LOG_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
    deleted = 0
    for path in LOG_DIR.glob("*.json"):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d")
            if file_date < cutoff:
                path.unlink()
                deleted += 1
                logger.info(f"Purged old log: {path.name}")
        except ValueError:
            pass  # skip non-date files
    if deleted:
        logger.info(f"Purged {deleted} old log file(s) (retention: {LOG_RETENTION_DAYS} days)")


def _save_session_record(record: SessionRecord) -> None:
    """Simpan riwayat sesi ke logs/riwayat/YYYY-MM-DD.json."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path     = HISTORY_DIR / f"{date_str}.json"
    try:
        records = []
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
        records.append(record.to_dict())
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        logger.info(f"Session record saved: tx#{record.transaction_id}")
    except Exception as e:
        logger.warning(f"Session save failed: {e}")


def _load_session_history(n_days: int = LOG_RETENTION_DAYS) -> list:
    """Load semua riwayat sesi dari n hari terakhir."""
    records = []
    if not HISTORY_DIR.exists():
        return records
    today = datetime.now()
    for i in range(n_days):
        date = today - timedelta(days=i)
        path = HISTORY_DIR / f"{date.strftime('%Y-%m-%d')}.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    records.extend(json.load(f))
            except Exception as e:
                logger.warning(f"History load failed {path}: {e}")
    records.sort(key=lambda r: r.get("start_time", ""), reverse=True)
    return records


def _purge_old_history() -> None:
    """Hapus file riwayat yang lebih dari 7 hari."""
    if not HISTORY_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
    for path in HISTORY_DIR.glob("*.json"):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d")
            if file_date < cutoff:
                path.unlink()
                logger.info(f"Purged old history: {path.name}")
        except ValueError:
            pass


class CPBridge:

    def __init__(self):
        self._cp           = None
        self._ws           = None
        self._status       = CPStatus.DISCONNECTED
        self._error        = ""
        self._connected_at = None
        # Load persisted logs from disk (last 7 days)
        _purge_old_logs()
        _purge_old_history()
        self._logs: List[LogEntry] = _load_logs_last_n_days(LOG_RETENTION_DAYS)
        self._notifications: Deque[RemoteNotification] = deque(maxlen=50)
        self._lock         = threading.Lock()
        self._stop_event   = threading.Event()

        from charge_point import NUMBER_OF_CONNECTORS
        self._connectors: dict = {
            i: ConnectorBridgeState(i) for i in range(1, NUMBER_OF_CONNECTORS + 1)
        }

        from charge_point import CSMS_URL, CHARGE_POINT_ID, CHARGE_POINT_MODEL, CHARGE_POINT_VENDOR
        self.csms_url  = CSMS_URL
        self.cp_id     = CHARGE_POINT_ID
        self.cp_model  = CHARGE_POINT_MODEL
        self.cp_vendor = CHARGE_POINT_VENDOR

        self.connect()

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def status(self): return self._status

    @property
    def is_connected(self): return self._status == CPStatus.CONNECTED

    @property
    def error(self): return self._error

    @property
    def connected_at(self): return self._connected_at

    @property
    def logs(self):
        with self._lock: return list(self._logs)

    @property
    def notifications(self):
        with self._lock: return list(self._notifications)

    @property
    def unread_count(self):
        with self._lock: return sum(1 for n in self._notifications if not n.read)

    def mark_all_read(self):
        with self._lock:
            for n in self._notifications: n.read = True

    def connector(self, cid: int) -> ConnectorBridgeState:
        return self._connectors.get(cid, ConnectorBridgeState(cid))

    def connector_status(self, cid: int) -> str:
        return self._connectors.get(cid, ConnectorBridgeState(cid)).status

    def active_txn(self, cid: int) -> Optional[int]:
        return self._connectors.get(cid, ConnectorBridgeState(cid)).transaction_id

    def meter_wh(self, cid: int) -> float:
        return self._connectors.get(cid, ConnectorBridgeState(cid)).meter_wh

    def soc_pct(self, cid: int) -> float:
        return self._connectors.get(cid, ConnectorBridgeState(cid)).soc_pct

    def chart_points(self, cid: int) -> list:
        """Return list of ChartPoint dicts for current session."""
        return list(self._connectors.get(cid, ConnectorBridgeState(cid)).chart_points)

    @property
    def session_history(self) -> list:
        """Return all session records from last 7 days."""
        return _load_session_history(LOG_RETENTION_DAYS)

    def temperature_c(self, cid: int) -> float:
        return self._connectors.get(cid, ConnectorBridgeState(cid)).temperature_c

    # ── Connect / Disconnect ───────────────────────────────────────────────────

    def connect(self):
        if self._status in (CPStatus.CONNECTED, CPStatus.CONNECTING): return
        self._status = CPStatus.CONNECTING
        self._error  = ""
        self._stop_event.clear()
        asyncio.run_coroutine_threadsafe(self._run(), get_loop())

    def disconnect(self):
        self._stop_event.set()
        ws = self._ws
        if ws: asyncio.run_coroutine_threadsafe(ws.close(), get_loop())
        shared_cp.set(None)
        self._cp = self._ws = None
        self._status = CPStatus.DISCONNECTED
        self._connected_at = None
        self._add_log("system", "System", "🔌 Disconnected")

    # ── Actions ───────────────────────────────────────────────────────────────

    def do_authorize(self, id_tag):
        self._run_action(self._cp.send_authorize(id_tag))

    def do_boot_notification(self):
        self._run_action(self._cp.send_boot_notification())

    def do_heartbeat(self):
        self._run_action(self._cp._trigger_heartbeat())

    def do_status_notification(self, connector_id: int, status: str):
        from ocpp.v16.enums import ChargePointStatus
        st_map = {"Available": ChargePointStatus.available,
                  "Preparing": ChargePointStatus.preparing,
                  "Charging":  ChargePointStatus.charging,
                  "Faulted":   ChargePointStatus.faulted,
                  "Unavailable": ChargePointStatus.unavailable}
        self._run_action(self._cp.send_status_notification(
            st_map.get(status, ChargePointStatus.available), connector_id=connector_id))
        if connector_id in self._connectors:
            self._connectors[connector_id].status = status

    def do_start_transaction(self, connector_id: int, id_tag: str, meter_start: int = 0):
        c = self._connectors.get(connector_id)
        if c:
            c.id_tag       = id_tag
            c.meter_wh     = float(meter_start)
            c.session_start = datetime.now()
            c.soc_at_start  = c.soc_pct
            c.max_temp_c    = c.temperature_c
            c.chart_points  = []

        async def _start():
            from ocpp.v16.enums import ChargePointStatus
            await self._cp.send_status_notification(
                ChargePointStatus.preparing, connector_id=connector_id)
            # txn_id captured via on_txn_id_received_cb callback
            await self._cp.send_start_transaction(connector_id, id_tag, meter_start)
            await self._cp.send_status_notification(
                ChargePointStatus.charging, connector_id=connector_id)
        self._run_action(_start())

    def do_stop_transaction(self, connector_id: int, id_tag: str = ""):
        from ocpp.v16.enums import Reason
        c = self._connectors.get(connector_id)
        if not c or c.transaction_id is None: return
        txn_id = c.transaction_id; meter_stop = int(c.meter_wh)

        async def _stop():
            from ocpp.v16.enums import ChargePointStatus
            await self._cp.send_stop_transaction(
                txn_id, meter_stop, Reason.local, connector_id=connector_id)
            await self._cp.send_status_notification(
                ChargePointStatus.available, connector_id=connector_id)
        self._run_action(_stop())
        # Build and save session record before resetting state
        if c.session_start:
            end_time   = datetime.now()
            duration_s = int((end_time - c.session_start).total_seconds())
            energy_del = c.meter_wh - (c.soc_at_start / 100 * 1)  # delta Wh
            avg_power  = (c.meter_wh / (duration_s / 3600)) if duration_s > 0 else 0
            record = SessionRecord(
                connector_id   = connector_id,
                transaction_id = txn_id,
                id_tag         = c.id_tag or "",
                start_time     = c.session_start.isoformat(),
                end_time       = end_time.isoformat(),
                duration_s     = duration_s,
                energy_wh      = c.meter_wh,
                soc_start      = c.soc_at_start,
                soc_end        = c.soc_pct,
                avg_power_w    = avg_power,
                max_temp_c     = c.max_temp_c,
                chart_points   = list(c.chart_points),
            )
            _save_session_record(record)
        c.transaction_id = None; c.id_tag = None
        c.status = "Available"; c.soc_pct = 0.0; c.temperature_c = 0.0
        c.session_start = None; c.chart_points = []

    def do_meter_values(self, connector_id: int, energy_wh: float,
                        power_w=0, voltage_v=0, current_a=0,
                        temperature_c=0, soc_pct=0):
        c = self._connectors.get(connector_id)
        if c:
            c.meter_wh = energy_wh
            if soc_pct:       c.soc_pct       = soc_pct
            if temperature_c:
                c.temperature_c = temperature_c
                if temperature_c > c.max_temp_c:
                    c.max_temp_c = temperature_c
            # Catat titik chart
            c.chart_points.append(ChartPoint(
                ts            = datetime.now().strftime('%H:%M:%S'),
                soc_pct       = round(soc_pct or c.soc_pct, 1),
                power_w       = round(power_w, 1),
                energy_wh     = round(energy_wh, 1),
                temperature_c = round(temperature_c or c.temperature_c, 1),
            ).to_dict())
            if len(c.chart_points) > 500:
                c.chart_points = c.chart_points[-500:]
        txn_id = c.transaction_id if c else 0
        self._run_action(self._cp.send_meter_values(
            txn_id, energy_wh, power_w, voltage_v, current_a,
            temperature_c, soc_pct, connector_id=connector_id))

    # ── Internal ───────────────────────────────────────────────────────────────

    def _run_action(self, coro):
        if not self.is_connected or self._cp is None: return
        asyncio.run_coroutine_threadsafe(coro, get_loop())

    async def _run(self):
        url = f"{self.csms_url.rstrip('/')}/{self.cp_id}"
        try:
            async with websockets.connect(url, subprotocols=["ocpp1.6"]) as ws:
                self._ws = ws; shared_cp.set(ws)
                from charge_point import ChargePoint
                cp_inst = ChargePoint(self.cp_id, ws)
                self._cp = cp_inst

                # Pasang semua callback hooks
                cp_inst.on_txn_id_received_cb = self._on_txn_id_received
                cp_inst.on_remote_start_cb    = self._on_remote_start
                cp_inst.on_remote_stop_cb     = self._on_remote_stop
                cp_inst.on_change_avail_cb    = self._on_change_avail
                cp_inst.on_reset_cb           = self._on_reset

                self._patch_logging(cp_inst)
                self._status = CPStatus.CONNECTED
                self._connected_at = datetime.now()
                self._add_log("system", "System", f"✅ Connected → {url}")

                listener = asyncio.create_task(cp_inst.start())
                interval = await cp_inst.send_boot_notification()
                hb = interval or 30
                from ocpp.v16.enums import ChargePointStatus
                await cp_inst.send_status_notification(ChargePointStatus.available, connector_id=0)
                for cid in self._connectors:
                    await cp_inst.send_status_notification(ChargePointStatus.available, connector_id=cid)
                    self._connectors[cid].status = "Available"
                asyncio.create_task(self._heartbeat_loop(cp_inst, hb))
                await listener

        except InvalidStatusCode as e:
            self._status = CPStatus.ERROR
            self._error  = f"HTTP {e.status_code}: rejected"
            self._add_log("system", "Error", f"❌ HTTP {e.status_code}")
        except ConnectionRefusedError:
            self._status = CPStatus.ERROR; self._error = "Connection refused"
            self._add_log("system", "Error", "❌ Connection refused")
        except Exception as e:
            self._status = CPStatus.ERROR; self._error = str(e)
            self._add_log("system", "Error", f"❌ {e}")
        finally:
            shared_cp.set(None); self._ws = None; self._cp = None
            if not self._stop_event.is_set():
                self._status = CPStatus.DISCONNECTED

    async def _heartbeat_loop(self, cp_inst, interval: int):
        while self.is_connected:
            await asyncio.sleep(interval)
            if self.is_connected: await cp_inst._trigger_heartbeat()

    # ── Callbacks dari async thread ────────────────────────────────────────────

    def _on_txn_id_received(self, connector_id: int, txn_id: int):
        c = self._connectors.get(connector_id)
        if c: c.transaction_id = txn_id; c.status = "Charging"
        self._add_log("system", "TxnID", f"✅ Connector {connector_id} → TxnID: {txn_id}")

    def _on_remote_start(self, connector_id: int, id_tag: str):
        c = self._connectors.get(connector_id)
        if c: c.id_tag = id_tag; c.status = "Preparing"
        with self._lock:
            self._notifications.appendleft(
                RemoteNotification("remote_start", {"connector_id": connector_id, "id_tag": id_tag}))
        self._add_log("system", "RemoteStart", f"⚡ CSMS: RemoteStart → connector {connector_id}, tag {id_tag}")

    def _on_remote_stop(self, connector_id, transaction_id: int):
        notif = RemoteNotification("remote_stop", {"connector_id": connector_id, "transaction_id": transaction_id})
        with self._lock: self._notifications.appendleft(notif)
        self._add_log("system", "RemoteStop", f"🛑 CSMS: RemoteStop → tx {transaction_id}")
        c = self._connectors.get(connector_id)
        if c: c.transaction_id = None; c.status = "Available"; c.soc_pct = 0.0

    def _on_change_avail(self, connector_id: int, status_str: str):
        if connector_id == 0:
            for c in self._connectors.values(): c.status = status_str
        elif connector_id in self._connectors:
            self._connectors[connector_id].status = status_str
        with self._lock:
            self._notifications.appendleft(
                RemoteNotification("change_avail", {"connector_id": connector_id, "status": status_str}))
        self._add_log("system", "ChangeAvail", f"🔄 CSMS: ChangeAvail → connector {connector_id} = {status_str}")

    def _on_reset(self, reset_type: str):
        with self._lock:
            self._notifications.appendleft(RemoteNotification("reset", {"type": reset_type}))
        self._add_log("system", "Reset", f"🔁 CSMS: Reset({reset_type})")

    # ── Log patch ──────────────────────────────────────────────────────────────

    def _patch_logging(self, cp_inst):
        orig_send = cp_inst._send; orig_route = cp_inst.route_message; bridge = self

        async def ps(message):
            try:
                import json; p = json.loads(message)
                bridge._add_log("out", p[2] if len(p)>2 and p[0]==2 else "Response", message)
            except: bridge._add_log("out", "Send", message)
            await orig_send(message)

        async def pr(raw):
            try:
                import json; p = json.loads(raw)
                bridge._add_log("in", p[2] if p[0]==2 else ("Response" if p[0]==3 else "Error"), raw)
            except: bridge._add_log("in", "Recv", raw)
            await orig_route(raw)

        cp_inst._send = ps; cp_inst.route_message = pr

    def _add_log(self, direction, action, raw):
        entry = LogEntry(direction, action, raw)
        with self._lock:
            self._logs.append(entry)
            # Keep at most 2000 entries in memory (all 7 days)
            if len(self._logs) > 2000:
                self._logs = self._logs[-2000:]
        # Persist to disk (outside lock to avoid blocking)
        _save_log_entry(entry)

    def clear_logs(self, clear_files: bool = False):
        """Clear in-memory log. If clear_files=True, also delete log files."""
        with self._lock:
            self._logs.clear()
        if clear_files:
            try:
                for path in LOG_DIR.glob("*.json"):
                    path.unlink()
                logger.info("All log files deleted")
            except Exception as e:
                logger.warning(f"Log file delete failed: {e}")



# ── Graceful shutdown ─────────────────────────────────────────────────────────

def _graceful_shutdown():
    """
    Dipanggil saat proses dihentikan.
    Kirim StopTransaction untuk semua transaksi aktif lalu tutup WebSocket.
    """
    # PERBAIKAN AMAN: Cek apakah variabel 'bridge' sudah dibuat di global scope
    if 'bridge' not in globals() or bridge is None:
        logger.info("[Shutdown] Objek bridge belum diinisialisasi. Lewati pembersihan.")
        return

    # Gunakan pengecekan method atau status yang aman
    # Jika bridge menggunakan method, panggil bridge.is_connected(). Jika properti, biarkan tanpa kurung.
    is_connected = bridge.is_connected() if callable(bridge.is_connected) else bridge.is_connected
    if not is_connected:
        return

    logger.info("[Shutdown] Menghentikan semua transaksi aktif…")

    loop = get_loop()

    # Iterasi semua connector — gunakan _connectors, bukan _active_txn
    if hasattr(bridge, '_connectors') and bridge._connectors:
        for cid, conn_state in list(bridge._connectors.items()):
            txn_id = getattr(conn_state, 'transaction_id', None)
            if txn_id and getattr(bridge, '_cp', None):
                logger.info(f"  StopTransaction CON {cid} tx#{txn_id}")
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        bridge._cp.send_stop_transaction(
                            txn_id,
                            int(getattr(conn_state, 'meter_wh', 0)),
                        ),
                        loop,
                    )
                    fut.result(timeout=5)
                except Exception as e:
                    logger.warning(f"  StopTransaction gagal: {e}")

    # Tutup WebSocket dengan benar agar CSMS tahu koneksi putus
    ws = getattr(bridge, '_ws', None)
    if ws:
        try:
            fut = asyncio.run_coroutine_threadsafe(ws.close(), loop)
            fut.result(timeout=3)
        except Exception:
            pass

    shared_cp.set(None)
    bridge._status = CPStatus.DISCONNECTED
    logger.info("[Shutdown] Selesai — WebSocket ditutup.")

# Daftarkan ke SIGTERM: dipanggil saat kill / systemctl stop / docker stop
def _sigterm_handler(signum, frame):
    _graceful_shutdown()
    raise SystemExit(0)

try:
    signal.signal(signal.SIGTERM, _sigterm_handler)
except (OSError, ValueError):
    pass  # tidak semua environment support signal registration


bridge = CPBridge()
