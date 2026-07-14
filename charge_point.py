"""
Charge Point - OCPP 1.6 Client
================================
Handler remote command yang didukung:
    - Reset (Soft/Hard), RemoteStartTransaction, RemoteStopTransaction
    - ChangeAvailability, UnlockConnector, GetConfiguration
    - ChangeConfiguration, ClearCache, TriggerMessage
"""

import asyncio
import logging
from datetime import datetime, timezone

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as cp
from ocpp.v16 import call, call_result
from ocpp.v16.enums import (
    Action, AvailabilityStatus, ChargePointStatus, ConfigurationStatus,
    Measurand, Reason, RegistrationStatus, TriggerMessageStatus,
    UnlockStatus, UnitOfMeasure,
)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [CP] %(levelname)s: %(message)s", datefmt="%H:%M:%S")

CSMS_URL             = "ws://10.10.60.186:8887"
CHARGE_POINT_ID      = "Ridho_TCS"
CHARGE_POINT_MODEL   = "SmartCharger-7kW"
CHARGE_POINT_VENDOR  = "IndonesiaEV"
ID_TAG               = "A56DEF4B"
NUMBER_OF_CONNECTORS = 2

from ocpp.v16.enums import ChargePointStatus
# Semua status yang valid di OCPP 1.6
CONNECTOR_STATUSES = [
    ChargePointStatus.available,
    ChargePointStatus.preparing,
    ChargePointStatus.charging,
    ChargePointStatus.suspended_evse,
    ChargePointStatus.suspended_ev,
    ChargePointStatus.finishing,
    ChargePointStatus.reserved,
    ChargePointStatus.unavailable,
    ChargePointStatus.faulted,
]

_local_config: dict = {
    "HeartbeatInterval": "30", "MeterValueSampleInterval": "30",
    "ConnectionTimeOut": "60", "AuthorizeRemoteTxRequests": "false",
    "LocalAuthorizeOffline": "true", "AllowOfflineTxForUnknownId": "false",
    "TransactionMessageAttempts": "3", "TransactionMessageRetryInterval": "60",
    "ResetRetries": "3", "NumberOfConnectors": str(NUMBER_OF_CONNECTORS),
    "SupportedFeatureProfiles": "Core,RemoteTrigger,SmartCharging",
}


class ConnectorState:
    """State independen untuk setiap konektor fisik."""
    def __init__(self, cid: int):
        self.cid = cid
        self.status = ChargePointStatus.available
        self.transaction_id = None
        self.id_tag = None
        self.meter_wh = 0.0
        self.soc_pct = 0.0
        self.temperature_c = 0.0

    @property
    def is_charging(self):
        return self.transaction_id is not None


_connectors: dict = {i: ConnectorState(i) for i in range(NUMBER_OF_CONNECTORS + 1)}



class ChargePoint(cp):

    # ── Callback hooks — diisi oleh cp_bridge ────────────────
    on_txn_id_received_cb          = None
    on_remote_start_cb             = None
    on_remote_stop_cb              = None
    on_change_avail_cb             = None
    on_reset_cb                    = None
    on_set_charging_profile_cb     = None
    on_clear_charging_profile_cb   = None
    on_get_composite_schedule_cb   = None
    # ── OUTBOUND ──────────────────────────────────────────────────────────────

    async def send_boot_notification(self):
        logging.info("Mengirim BootNotification...")
        response = await self.call(call.BootNotificationPayload(
            charge_point_model=CHARGE_POINT_MODEL,
            charge_point_vendor=CHARGE_POINT_VENDOR,
            charge_point_serial_number="SN-2024-001",
            firmware_version="v2.1.0",
        ))
        if response.status == RegistrationStatus.accepted:
            logging.info(f"BootNotification diterima! Interval: {response.interval}s")
            return response.interval
        return None

    async def send_heartbeat(self, interval: int):
        while True:
            await asyncio.sleep(interval)
            await self._trigger_heartbeat()

    async def _trigger_heartbeat(self):
        logging.info("Mengirim Heartbeat...")
        await self.call(call.HeartbeatPayload())

    async def send_status_notification(self, status: ChargePointStatus, connector_id: int = 1):
        logging.info(f"Status → connector {connector_id}: {status.value}")
        if connector_id in _connectors:
            _connectors[connector_id].status = status
        await self.call(call.StatusNotificationPayload(
            connector_id=connector_id, error_code="NoError",
            status=status, timestamp=datetime.now(timezone.utc).isoformat(),
        ))

    async def send_authorize(self, id_tag: str) -> bool:
        logging.info(f"Authorize id_tag: {id_tag}")
        response = await self.call(call.AuthorizePayload(id_tag=id_tag))
        return response.id_tag_info["status"] == "Accepted"

    async def send_start_transaction(self, connector_id: int, id_tag: str, meter_start: int = 0) -> int:
        logging.info(f"StartTransaction connector={connector_id} id_tag={id_tag}")
        response = await self.call(call.StartTransactionPayload(
            connector_id=connector_id, id_tag=id_tag,
            meter_start=meter_start, timestamp=datetime.now(timezone.utc).isoformat(),
        ))
        txn_id = response.transaction_id
        if connector_id in _connectors:
            _connectors[connector_id].transaction_id = txn_id
            _connectors[connector_id].id_tag = id_tag
            _connectors[connector_id].meter_wh = float(meter_start)
        # Notify bridge → UI auto-update
        if self.on_txn_id_received_cb:
            self.on_txn_id_received_cb(connector_id, txn_id)
        logging.info(f"Transaction ID={txn_id} untuk connector {connector_id}")
        return txn_id

    async def send_stop_transaction(self, transaction_id: int, meter_stop: int,
                                    reason: Reason, connector_id=None):
        logging.info(f"StopTransaction tx_id={transaction_id}")
        await self.call(call.StopTransactionPayload(
            meter_stop=meter_stop, timestamp=datetime.now(timezone.utc).isoformat(),
            transaction_id=transaction_id, reason=reason,
        ))
        cid = connector_id
        if cid is None:
            for c in _connectors.values():
                if c.transaction_id == transaction_id:
                    cid = c.cid; break
        if cid and cid in _connectors:
            _connectors[cid].transaction_id = None
            _connectors[cid].id_tag = None

    async def send_meter_values(self, transaction_id: int, energy_wh: float,
                                 power_w=0.0, voltage_v=0.0, current_a=0.0,
                                 temperature_c=0.0, soc_pct=0.0, connector_id: int = 1):
        logging.info(f"MeterValues connector={connector_id}: {energy_wh:.1f}Wh SoC={soc_pct:.1f}%")
        sampled = [{"value": str(round(energy_wh, 2)),
                    "measurand": Measurand.energy_active_import_register,
                    "unit": UnitOfMeasure.wh}]
        if power_w:       sampled.append({"value": str(round(power_w,2)),       "measurand": Measurand.power_active_import,            "unit": UnitOfMeasure.w})
        if voltage_v:     sampled.append({"value": str(round(voltage_v,2)),     "measurand": Measurand.voltage,                        "unit": UnitOfMeasure.v})
        if current_a:     sampled.append({"value": str(round(current_a,2)),     "measurand": Measurand.current_import,                 "unit": UnitOfMeasure.a})
        if temperature_c: sampled.append({"value": str(round(temperature_c,2)),"measurand": Measurand.temperature,                    "unit": UnitOfMeasure.celsius})
        if soc_pct:       sampled.append({"value": str(round(soc_pct,2)),       "measurand": Measurand.soc,                            "unit": UnitOfMeasure.percent})
        await self.call(call.MeterValuesPayload(
            connector_id=connector_id, transaction_id=transaction_id,
            meter_value=[{"timestamp": datetime.now(timezone.utc).isoformat(),
                          "sampled_value": sampled}],
        ))

    # ── INBOUND ───────────────────────────────────────────────────────────────

    @on(Action.Reset)
    async def on_reset(self, type, **kwargs):
        logging.info(f"[REMOTE] Reset({type})")
        if self.on_reset_cb: self.on_reset_cb(type)
        return call_result.Reset(status="Accepted")

    @on(Action.RemoteStartTransaction)
    async def on_remote_start(self, id_tag, connector_id=None, **kwargs):
        cid = connector_id
        if cid is None:
            for c in _connectors.values():
                if c.cid > 0 and not c.is_charging:
                    cid = c.cid; break
        if cid is None:
            return call_result.RemoteStartTransaction(status="Rejected")
        logging.info(f"[REMOTE] RemoteStart id_tag={id_tag} connector={cid}")
        if self.on_remote_start_cb: self.on_remote_start_cb(cid, id_tag)
        asyncio.create_task(self._do_remote_start(cid, id_tag))
        return call_result.RemoteStartTransaction(status="Accepted")

    @on(Action.RemoteStopTransaction)
    async def on_remote_stop(self, transaction_id, **kwargs):
        logging.info(f"[REMOTE] RemoteStop tx_id={transaction_id}")
        target_cid = None
        for c in _connectors.values():
            if c.transaction_id == transaction_id:
                target_cid = c.cid; break
        if self.on_remote_stop_cb: self.on_remote_stop_cb(target_cid, transaction_id)
        if target_cid: asyncio.create_task(self._do_remote_stop(target_cid, transaction_id))
        return call_result.RemoteStopTransaction(status="Accepted")

    @on(Action.ChangeAvailability)
    async def on_change_availability(self, connector_id, type, **kwargs):
        logging.info(f"[REMOTE] ChangeAvailability connector={connector_id} type={type}")
        new_st = ChargePointStatus.available if type == "Operative" else ChargePointStatus.unavailable
        st_str = "Available" if type == "Operative" else "Unavailable"
        if self.on_change_avail_cb: self.on_change_avail_cb(connector_id, st_str)
        if connector_id == 0:
            for c in _connectors.values():
                if c.cid > 0:
                    asyncio.create_task(self.send_status_notification(new_st, connector_id=c.cid))
        else:
            asyncio.create_task(self.send_status_notification(new_st, connector_id=connector_id))
        return call_result.ChangeAvailability(status=AvailabilityStatus.accepted)

    @on(Action.UnlockConnector)
    async def on_unlock_connector(self, connector_id, **kwargs):
        c = _connectors.get(connector_id)
        if c and c.is_charging:
            return call_result.UnlockConnector(status=UnlockStatus.unlock_failed)
        return call_result.UnlockConnector(status=UnlockStatus.unlocked)

    @on(Action.GetConfiguration)
    async def on_get_configuration(self, key=None, **kwargs):
        keys = key if key else list(_local_config.keys())
        cfg, unk = [], []
        for k in keys:
            if k in _local_config: cfg.append({"key": k, "readonly": False, "value": _local_config[k]})
            else: unk.append(k)
        return call_result.GetConfiguration(configuration_key=cfg, unknown_key=unk or None)

    @on(Action.ChangeConfiguration)
    async def on_change_configuration(self, key, value, **kwargs):
        if key in _local_config:
            _local_config[key] = value
            return call_result.ChangeConfiguration(status=ConfigurationStatus.accepted)
        return call_result.ChangeConfiguration(status=ConfigurationStatus.not_supported)

    @on(Action.ClearCache)
    async def on_clear_cache(self, **kwargs):
        return call_result.ClearCache(status="Accepted")

    @on(Action.TriggerMessage)
    async def on_trigger_message(self, requested_message, connector_id=None, **kwargs):
        try:
            if requested_message == "BootNotification":
                asyncio.create_task(self.send_boot_notification())
            elif requested_message == "Heartbeat":
                asyncio.create_task(self._trigger_heartbeat())
            elif requested_message == "StatusNotification":
                cid = connector_id or 1
                asyncio.create_task(self.send_status_notification(_connectors[cid].status, connector_id=cid))
            else:
                return call_result.TriggerMessage(status=TriggerMessageStatus.not_implemented)
            return call_result.TriggerMessage(status=TriggerMessageStatus.accepted)
        except Exception as e:
            logging.error(f"[REMOTE] TriggerMessage error: {e}")
            return call_result.TriggerMessage(status=TriggerMessageStatus.rejected)
    @on(Action.SetChargingProfile)
    async def on_set_charging_profile(self, connector_id,
                                      cs_charging_profiles, **kwargs):
        logging.info(
            f"[SMART CHARGING] SetChargingProfile → connector {connector_id}, "
            f"purpose: {cs_charging_profiles.get('chargingProfilePurpose','?')}"
        )
        if self.on_set_charging_profile_cb:
            self.on_set_charging_profile_cb(connector_id, cs_charging_profiles)
        return call_result.SetChargingProfilePayload(status="Accepted")

    @on(Action.ClearChargingProfile)
    async def on_clear_charging_profile(self, id=None, connector_id=None,
                                        charging_profile_purpose=None,
                                        stack_level=None, **kwargs):
        logging.info(
            f"[SMART CHARGING] ClearChargingProfile → connector {connector_id}, id {id}"
        )
        if self.on_clear_charging_profile_cb:
            self.on_clear_charging_profile_cb(connector_id, id)
        return call_result.ClearChargingProfilePayload(status="Accepted")

    @on(Action.GetCompositeSchedule)
    async def on_get_composite_schedule(self, connector_id, duration,
                                        charging_rate_unit=None, **kwargs):
        logging.info(
            f"[SMART CHARGING] GetCompositeSchedule → connector {connector_id}, "
            f"duration {duration}s"
        )
        if self.on_get_composite_schedule_cb:
            result = self.on_get_composite_schedule_cb(connector_id, duration)
        else:
            result = {"status": "Rejected"}

        if result.get("status") == "Accepted":
            return call_result.GetCompositeSchedulePayload(
                status            = "Accepted",
                connector_id      = result.get("connectorId", connector_id),
                schedule_start    = result.get("scheduleStart"),
                charging_schedule = result.get("chargingSchedule"),
            )
        return call_result.GetCompositeSchedulePayload(status="Rejected")

    # ── Remote helpers ────────────────────────────────────────────────────────

    async def _do_remote_start(self, connector_id: int, id_tag: str):
        await self.send_status_notification(ChargePointStatus.preparing, connector_id=connector_id)
        await asyncio.sleep(0.5)
        txn_id = await self.send_start_transaction(connector_id, id_tag, meter_start=0)
        await self.send_status_notification(ChargePointStatus.charging, connector_id=connector_id)
        logging.info(f"[REMOTE] Charging aktif — connector={connector_id} tx={txn_id}")

    async def _do_remote_stop(self, connector_id: int, transaction_id: int):
        c = _connectors.get(connector_id)
        meter_stop = int(c.meter_wh) if c else 0
        await self.send_stop_transaction(transaction_id, meter_stop, Reason.remote, connector_id=connector_id)
        await self.send_status_notification(ChargePointStatus.available, connector_id=connector_id)
        logging.info(f"[REMOTE] Connector {connector_id} kembali Available")


async def main():
    url = f"{CSMS_URL}/{CHARGE_POINT_ID}"
    logging.info(f"Menghubungkan ke: {url}")
    try:
        async with websockets.connect(url, subprotocols=["ocpp1.6"]) as ws:
            cp_inst = ChargePoint(CHARGE_POINT_ID, ws)
            daemon = asyncio.create_task(cp_inst.start())
            interval = await cp_inst.send_boot_notification()
            hb = interval or 30
            await cp_inst.send_status_notification(ChargePointStatus.available, connector_id=0)
            for cid in range(1, NUMBER_OF_CONNECTORS + 1):
                await cp_inst.send_status_notification(ChargePointStatus.available, connector_id=cid)
            asyncio.create_task(cp_inst.send_heartbeat(hb))
            logging.info("Standby. Menunggu perintah dari CSMS...")
            await daemon
    except Exception as e:
        logging.error(f"Koneksi terputus atau error: {e}")


if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
