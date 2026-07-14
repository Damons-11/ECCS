"""
OCPP Charge Point Simulator
Single-file version: UI + logic in one app.py
"""

import json
import time
import base64
import os
import streamlit as st
from datetime import datetime

from cp_bridge import bridge, CPStatus
import pandas as pd
from charge_point import NUMBER_OF_CONNECTORS
from ev_simulator import start_ev_sim, stop_ev_sim, get_active_sim, DEFAULT_ID_TAG, DEFAULT_BATTERY_KWH, DEFAULT_MAX_POWER_W, DEFAULT_METER_INTERVAL_S

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ECCS · OCPP Terminal",
    page_icon="🌙",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Session state ─────────────────────────────────────────────────────────────
if "active_overlay"   not in st.session_state:
    st.session_state.active_overlay   = None
if "active_connector" not in st.session_state:
    st.session_state.active_connector = 1

# ── Load gif asset ────────────────────────────────────────────────────────────
_gif_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "vibing-aigis.gif")
if os.path.exists(_gif_path):
    with open(_gif_path, "rb") as _f:
        GIF_SRC = f"data:image/gif;base64,{base64.b64encode(_f.read()).decode()}"
else:
    GIF_SRC = ""

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600;700&family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap');

:root {
  --p3-navy:   #030b1a;
  --p3-dark:   #060f22;
  --p3-blue:   #0a1f4e;
  --p3-accent: #1a50c8;
  --p3-bright: #2d7fff;
  --p3-gold:   #c8a84b;
  --p3-white:  #e8edf8;
  --p3-dim:    #6e7fa0;
  --p3-green:  #2dff8f;
  --p3-red:    #ff3355;
  --p3-yellow: #ffe066;
  --p3-teal:   #00d4cc;
}

/* ── Reset & base ── */
*, *::before, *::after { box-sizing: border-box; }
.stApp { background: var(--p3-navy) !important; font-family: 'Rajdhani', sans-serif !important; color: var(--p3-white) !important; }
#MainMenu, footer, header { visibility: hidden; }

/* ── FULLSCREEN: remove all Streamlit padding ── */
.block-container {
  padding: 0 !important;
  max-width: 100vw !important;
  min-height: 100vh !important;
}
[data-testid="stSidebar"]          { display: none !important; }
[data-testid="stAppViewContainer"] { padding: 0 !important; }
[data-testid="stMain"]             { padding: 0 !important; }
section[data-testid="stMain"] > div:first-child { padding: 0 !important; }

/* ── Widget overrides ── */
label, .stTextInput label, .stNumberInput label {
  color: var(--p3-dim) !important;
  font-family: 'Share Tech Mono', monospace !important;
  font-size: 10px !important;
  text-transform: uppercase !important;
  letter-spacing: 1px !important;
}
input, .stTextInput input, .stNumberInput input {
  background: rgba(10,31,78,0.7) !important;
  border: 1px solid var(--p3-accent) !important;
  color: var(--p3-white) !important;
  font-family: 'Share Tech Mono', monospace !important;
  border-radius: 3px !important;
  font-size: 12px !important;
  padding: 4px 8px !important;
}
input:focus { border-color: var(--p3-bright) !important; box-shadow: 0 0 6px rgba(45,127,255,0.4) !important; outline: none !important; }
input[type=number]::-webkit-inner-spin-button,
input[type=number]::-webkit-outer-spin-button { -webkit-appearance:none; margin:0; }
input[type=number] { -moz-appearance: textfield; }
.stNumberInput [data-testid="stNumberInputStepDown"],
.stNumberInput [data-testid="stNumberInputStepUp"] { display:none !important; }
.stButton > button {
  font-family: 'Orbitron', sans-serif !important;
  font-size: 10px !important; letter-spacing: 2px !important;
  font-weight: 700 !important; border-radius: 3px !important;
  transition: all .12s !important; padding: 4px 10px !important;
}
.stButton > button[kind="primary"] {
  background: var(--p3-accent) !important;
  border: 1px solid var(--p3-bright) !important; color: #fff !important;
  box-shadow: 0 0 10px rgba(45,127,255,0.25) !important;
}
.stButton > button[kind="primary"]:hover { background: var(--p3-bright) !important; box-shadow: 0 0 18px rgba(45,127,255,0.5) !important; }
.stButton > button[kind="secondary"] {
  background: transparent !important; border: 1px solid var(--p3-dim) !important; color: var(--p3-dim) !important;
}
.stButton > button[kind="secondary"]:hover { border-color: var(--p3-bright) !important; color: var(--p3-bright) !important; }
.stRadio label { color: var(--p3-white) !important; font-size: 11px !important; }

/* ── Scan lines ── */
.p3-scan {
  position: fixed; inset: 0; pointer-events: none; z-index: 999;
  background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.025) 2px, rgba(0,0,0,0.025) 4px);
}

/* ── Top bar ── */
.p3-topbar {
  width:100%; height:32px;
  background: linear-gradient(90deg, var(--p3-navy), var(--p3-blue), var(--p3-navy));
  border-bottom: 2px solid var(--p3-accent);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 16px;
  font-family: 'Share Tech Mono', monospace; font-size: 10px;
  color: var(--p3-dim); letter-spacing: 2px;
  flex-shrink: 0;
}
.p3-topbar-logo { font-family:'Orbitron',sans-serif; font-size:12px; font-weight:900; color:var(--p3-gold); letter-spacing:4px; }

/* ── Full-height layout ── */
.p3-layout {
  display: flex; flex-direction: column;
  height: calc(100vh - 32px);   /* topbar=32px */
  overflow: hidden;
}
.p3-tabrow {
  flex-shrink: 0;
  background: rgba(6,15,34,0.98);
  border-bottom: 1px solid rgba(45,127,255,0.2);
  padding: 2px 8px 0;
}
.p3-statusstrip {
  flex-shrink: 0;
  padding: 2px 10px;
  background: rgba(3,11,26,0.6);
  border-bottom: 1px solid rgba(45,127,255,0.1);
  font-family: 'Share Tech Mono', monospace; font-size: 10px;
  color: var(--p3-dim);
}
.p3-body {
  flex: 1 1 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0;
  overflow: hidden;
  min-height: 0;
}

/* ── Panels ── */
.p3-panel {
  overflow-y: auto;
  overflow-x: hidden;
  padding: 12px 14px;
  background: rgba(6,15,34,0.92);
  border-right: 1px solid rgba(45,127,255,0.15);
  position: relative;
  min-height: 0;
}
.p3-panel:last-child { border-right: none; }
.p3-panel::before {
  content: ''; position: absolute; inset: 0; pointer-events: none;
  background: repeating-linear-gradient(-45deg, transparent, transparent 30px, rgba(45,127,255,0.012) 30px, rgba(45,127,255,0.012) 31px);
}

/* ── Info ── */
.p3-section-title {
  font-family: 'Orbitron', sans-serif; font-size: 8px; font-weight: 700;
  color: var(--p3-dim); letter-spacing: 3px; text-transform: uppercase;
  border-bottom: 1px solid rgba(45,127,255,0.18); padding-bottom: 6px; margin-bottom: 8px;
}
.p3-info-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 3px 0; border-bottom: 1px solid rgba(255,255,255,0.03); font-size: 11px;
}
.p3-info-key { font-family:'Share Tech Mono',monospace; color:var(--p3-dim); font-size:9px; letter-spacing:1px; }
.p3-info-val { font-family:'Share Tech Mono',monospace; color:var(--p3-white); font-size:11px; }

/* ── Status badges ── */
.p3-status-badge {
  display:inline-flex; align-items:center; gap:5px; padding:2px 8px; border-radius:2px;
  font-family:'Orbitron',sans-serif; font-size:8px; font-weight:700; letter-spacing:2px;
}
.p3-badge-connected    { background:rgba(45,255,143,0.12); color:var(--p3-green); border:1px solid rgba(45,255,143,0.4); }
.p3-badge-disconnected { background:rgba(255,51,85,0.12);  color:var(--p3-red);   border:1px solid rgba(255,51,85,0.4); }
.p3-badge-connecting   { background:rgba(255,224,102,0.12);color:var(--p3-yellow);border:1px solid rgba(255,224,102,0.4); }
.p3-badge-error        { background:rgba(255,51,85,0.12);  color:var(--p3-red);   border:1px solid rgba(255,51,85,0.4); }

.p3-connector-badge {
  display:inline-block; padding:1px 6px; border-radius:2px;
  font-family:'Share Tech Mono',monospace; font-size:9px; font-weight:700; letter-spacing:1px;
}
.cst-available   { background:rgba(45,255,143,0.1);  color:var(--p3-green);  border:1px solid rgba(45,255,143,0.3); }
.cst-charging    { background:rgba(45,127,255,0.15); color:var(--p3-bright); border:1px solid rgba(45,127,255,0.4); animation:cst-blink 1.4s ease-in-out infinite; }
.cst-preparing   { background:rgba(255,224,102,0.1); color:var(--p3-yellow); border:1px solid rgba(255,224,102,0.3); }
.cst-faulted     { background:rgba(255,51,85,0.12);  color:var(--p3-red);    border:1px solid rgba(255,51,85,0.4); }
.cst-unavailable { background:rgba(110,127,160,0.12);color:var(--p3-dim);    border:1px solid rgba(110,127,160,0.3); }
.cst-suspended   { background:rgba(0,212,204,0.1);   color:var(--p3-teal);   border:1px solid rgba(0,212,204,0.3); }
.cst-finishing   { background:rgba(139,92,246,0.1);  color:#a78bfa;          border:1px solid rgba(139,92,246,0.3); }
.cst-reserved    { background:rgba(251,146,60,0.1);  color:#fb923c;          border:1px solid rgba(251,146,60,0.3); }
@keyframes cst-blink { 0%,100%{opacity:1;} 50%{opacity:.5;} }

/* ── Charge animation ── */
.p3-charge-anim {
  display:flex; flex-direction:column; align-items:center;
  justify-content:center; gap:8px; padding:16px 0 8px;
}
.p3-bolt { font-size:48px; line-height:1;
  filter:drop-shadow(0 0 14px var(--p3-bright));
  animation:bolt-anim 1.5s ease-in-out infinite; }
.p3-bolt.idle { filter:drop-shadow(0 0 6px var(--p3-dim)); animation:none; opacity:0.35; }
@keyframes bolt-anim {
  0%,100%{filter:drop-shadow(0 0 14px var(--p3-bright));transform:scale(1);}
  50%{filter:drop-shadow(0 0 28px var(--p3-bright)) drop-shadow(0 0 50px var(--p3-teal));transform:scale(1.06);}
}
.p3-status-text { font-family:'Orbitron',sans-serif; font-size:10px; font-weight:700; letter-spacing:3px; color:var(--p3-dim); text-transform:uppercase; }

/* ── SOC bar ── */
@keyframes shimmer { 0%{transform:translateX(-100%);} 100%{transform:translateX(200%);} }

/* ── Overlay ── */
.p3-overlay { background:rgba(3,11,26,0.97); border:1px solid var(--p3-accent); border-radius:3px; padding:14px 16px; position:relative; }
.p3-overlay::before { content:''; position:absolute; top:-1px; right:-1px; width:10px; height:10px; border-top:2px solid var(--p3-gold); border-right:2px solid var(--p3-gold); }
.p3-overlay-title { font-family:'Orbitron',sans-serif; font-size:9px; font-weight:700; color:var(--p3-gold); letter-spacing:3px; text-transform:uppercase; margin-bottom:12px; padding-bottom:6px; border-bottom:1px solid rgba(200,168,75,0.2); }

/* ── Log ── */
.p3-log-wrap { background:rgba(3,11,26,0.8); border:1px solid rgba(45,127,255,0.2); border-radius:3px; padding:8px; max-height:340px; overflow-y:auto; font-family:'Share Tech Mono',monospace; font-size:10px; }
.p3-log-out    { color:var(--p3-bright); border-left:2px solid var(--p3-accent); padding:2px 6px; margin:2px 0; }
.p3-log-in     { color:var(--p3-green);  border-left:2px solid var(--p3-green);  padding:2px 6px; margin:2px 0; }
.p3-log-system { color:var(--p3-dim);    border-left:2px solid rgba(110,127,160,0.3); padding:2px 6px; margin:2px 0; }
.p3-log-ts     { color:rgba(110,127,160,0.55); font-size:8px; margin-right:5px; }
.p3-log-action { font-weight:700; margin-right:5px; }

/* ── Notification ── */
.p3-notif { background:rgba(3,11,26,0.97); border:1px solid var(--p3-gold); border-radius:4px; padding:8px 12px; font-family:'Share Tech Mono',monospace; font-size:10px; color:var(--p3-white); margin-bottom:6px; animation:notif-in .25s ease; }
.p3-notif-unread { border-color:var(--p3-bright); }
@keyframes notif-in { from{opacity:0;transform:translateX(16px);} to{opacity:1;transform:translateX(0);} }

/* ── Splash ── */
.p3-logo-ring { width:80px; height:80px; border-radius:50%; border:3px solid var(--p3-gold); margin:0 auto 16px; display:flex; align-items:center; justify-content:center; font-size:32px; box-shadow:0 0 18px rgba(200,168,75,0.3); animation:p3-pulse 3s ease-in-out infinite; }
@keyframes p3-pulse { 0%,100%{box-shadow:0 0 18px rgba(200,168,75,0.3);} 50%{box-shadow:0 0 32px rgba(200,168,75,0.6),0 0 50px rgba(45,127,255,0.2);} }
.p3-title { font-family:'Orbitron',sans-serif; font-size:20px; font-weight:900; color:var(--p3-white); text-align:center; letter-spacing:4px; margin-bottom:4px; }
.p3-subtitle { font-family:'Share Tech Mono',monospace; font-size:9px; color:var(--p3-gold); text-align:center; letter-spacing:3px; margin-bottom:24px; text-transform:uppercase; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width:4px; }
::-webkit-scrollbar-track { background:rgba(10,31,78,0.2); }
::-webkit-scrollbar-thumb { background:rgba(45,127,255,0.3); border-radius:2px; }
::-webkit-scrollbar-thumb:hover { background:rgba(45,127,255,0.5); }
</style>
<div class="p3-scan"></div>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def badge_html(status):
    cls = {CPStatus.CONNECTED:"p3-badge-connected",CPStatus.DISCONNECTED:"p3-badge-disconnected",
           CPStatus.CONNECTING:"p3-badge-connecting",CPStatus.ERROR:"p3-badge-error"}.get(status,"p3-badge-disconnected")
    dot = {"CONNECTED":"🟢","DISCONNECTED":"🔴","CONNECTING":"🟡","ERROR":"🔴"}.get(status.name,"⚫")
    return f'<span class="p3-status-badge {cls}">{dot} {status.value}</span>'

def connector_badge(s):
    cls = {"Available":"cst-available","Charging":"cst-charging","Preparing":"cst-preparing",
           "Faulted":"cst-faulted","Unavailable":"cst-unavailable","SuspendedEVSE":"cst-suspended",
           "SuspendedEV":"cst-suspended","Finishing":"cst-finishing","Reserved":"cst-reserved"}.get(s,"cst-available")
    return f'<span class="p3-connector-badge {cls}">{s.upper()}</span>'

def render_log_entries():
    logs = bridge.logs
    if not logs:
        st.markdown('<div class="p3-log-wrap"><span style="color:var(--p3-dim);">— no messages —</span></div>', unsafe_allow_html=True)
        return
    parts = ['<div class="p3-log-wrap">']
    for e in reversed(logs[-80:]):
        ts  = e.timestamp.strftime("%H:%M:%S")
        css = {"out":"p3-log-out","in":"p3-log-in","system":"p3-log-system"}.get(e.direction,"p3-log-system")
        lbl = {"out":"▲","in":"▼","system":"·"}.get(e.direction,"·")
        try:
            body = json.dumps(json.loads(e.raw), indent=None, separators=(",",":"))
        except Exception:
            body = e.raw
        body = (body[:110]+"…" if len(body)>110 else body).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        parts.append(f'<div class="{css}"><span class="p3-log-ts">{ts}</span><span class="p3-log-action">{lbl} {e.action}</span><span>{body}</span></div>')
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)

all_cids = list(range(1, NUMBER_OF_CONNECTORS + 1))

# ══════════════════════════════════════════════════════════════════════════════
# SPLASH SCREENS
# ══════════════════════════════════════════════════════════════════════════════

if bridge.status == CPStatus.CONNECTING:
    st.markdown('<div class="p3-topbar"><span class="p3-topbar-logo">SEES · OCPP</span><span>CHARGE POINT TERMINAL</span><span></span></div>', unsafe_allow_html=True)
    st.markdown('<div style="height:100px"></div>', unsafe_allow_html=True)
    _, cc, _ = st.columns([1,1,1])
    with cc:
        st.markdown('<div class="p3-logo-ring">⚡</div><div class="p3-title">CHARGE POINT</div><div class="p3-subtitle">OCPP 1.6 · S.E.E.S TERMINAL</div><div style="text-align:center;font-family:\'Share Tech Mono\',monospace;color:var(--p3-yellow);font-size:11px;letter-spacing:4px;margin-top:20px;">● CONNECTING…</div>', unsafe_allow_html=True)
    time.sleep(0.5)
    st.rerun()

elif bridge.status == CPStatus.ERROR:
    st.markdown('<div class="p3-topbar"><span class="p3-topbar-logo">SEES · OCPP</span><span>CHARGE POINT TERMINAL</span><span></span></div>', unsafe_allow_html=True)
    st.markdown('<div style="height:100px"></div>', unsafe_allow_html=True)
    _, cc, _ = st.columns([1,1,1])
    with cc:
        st.markdown(f'<div class="p3-logo-ring" style="border-color:var(--p3-red);">⚠</div><div class="p3-title">CONNECTION ERROR</div><div class="p3-subtitle" style="color:var(--p3-red);">{bridge.error}</div>', unsafe_allow_html=True)
        if st.button("⟳  RETRY", type="primary", use_container_width=True):
            bridge.connect()
            st.rerun()

else:
    # ══════════════════════════════════════════════════════════════════════════
    # MAIN OVERLAY — FULLSCREEN
    # ══════════════════════════════════════════════════════════════════════════

    cid    = st.session_state.active_connector
    ov     = st.session_state.active_overlay
    cstat  = bridge.connector_status(cid)
    unread = bridge.unread_count

    # ── Top bar ───────────────────────────────────────────────────────────────
    now = datetime.now().strftime("%H:%M:%S")
    notif_badge = ""
    if unread > 0:
        notif_badge = f'<span style="background:var(--p3-red);color:#fff;font-family:\'Orbitron\',sans-serif;font-size:8px;font-weight:700;padding:1px 6px;border-radius:10px;margin-right:8px;animation:cst-blink 1s infinite;">🔔 {unread}</span>'
    st.markdown(
        f'<div class="p3-topbar"><span class="p3-topbar-logo">SEES · OCPP</span><span>CHARGE POINT TERMINAL</span><span>{notif_badge}{badge_html(bridge.status)}&nbsp;&nbsp;{now}</span></div>',
        unsafe_allow_html=True,
    )

    # ── Tab row ───────────────────────────────────────────────────────────────
    tab_cols = st.columns([0.5] * len(all_cids) + [2.5, 1, 1, 1, 1, 0.8, 0.8, 0.4])
    for i, c in enumerate(all_cids):
        with tab_cols[i]:
            if st.button(f"CON {c}", key=f"tab_ch{c}", type="primary" if cid==c else "secondary"):
                st.session_state.active_connector = c
                st.session_state.active_overlay   = None
                st.rerun()
    off = len(all_cids) + 1
    with tab_cols[off]:
        if st.button("AUTHORIZE", key="tab_auth", type="primary" if ov=="authorize" else "secondary"):
            st.session_state.active_overlay = None if ov=="authorize" else "authorize"
            st.rerun()
    with tab_cols[off+1]:
        if st.button("LOGS", key="tab_logs", type="primary" if ov=="logs" else "secondary"):
            st.session_state.active_overlay = None if ov=="logs" else "logs"
            st.rerun()
    with tab_cols[off+2]:
        if st.button("TEST", key="tab_test", type="primary" if ov=="test" else "secondary"):
            st.session_state.active_overlay = None if ov=="test" else "test"
            st.rerun()
    with tab_cols[off+3]:
        if st.button("RIWAYAT", key="tab_hist", type="primary" if ov=="history" else "secondary"):
            st.session_state.active_overlay = None if ov=="history" else "history"
            st.rerun()
    with tab_cols[off+4]:
        sim = get_active_sim()
        ev_label = "🚗 EV ●" if (sim and not sim._stop) else "🚗 EV"
        if st.button(ev_label, key="tab_ev", type="primary" if ov=="ev_sim" else "secondary"):
            st.session_state.active_overlay = None if ov=="ev_sim" else "ev_sim"
            st.rerun()
    with tab_cols[off+5]:
        nlbl = f"🔔 {unread}" if unread > 0 else "🔔"
        if st.button(nlbl, key="tab_notif", type="primary" if ov=="notifications" else "secondary"):
            st.session_state.active_overlay = None if ov=="notifications" else "notifications"
            bridge.mark_all_read()
            st.rerun()
    with tab_cols[off+6]:
        if st.button("✕", key="btn_dc", type="secondary"):
            bridge.disconnect()
            st.session_state.active_overlay = None
            st.rerun()

    # ── Status strip ──────────────────────────────────────────────────────────
    strip_parts = []
    for c in all_cids:
        cs   = bridge.connector_status(c)
        ctxn = bridge.active_txn(c)
        ttag = f" <span style=\'color:var(--p3-teal);font-size:8px;\'>&nbsp;#{ctxn}</span>" if ctxn else ""
        strip_parts.append(f"<span style=\'font-family:\'Share Tech Mono\',monospace;font-size:9px;color:var(--p3-dim);margin-right:14px;\'>CON{c}&nbsp;{connector_badge(cs)}{ttag}</span>")
    st.markdown(f'<div class="p3-statusstrip">{" ".join(strip_parts)}</div>', unsafe_allow_html=True)

    # ── Two-column body — fills remaining height ──────────────────────────────
    left_col, right_col = st.columns([1, 1], gap="small")

    # ════════════════════════════════════════════
    # LEFT PANEL
    # ════════════════════════════════════════════
    with left_col:
        # ── CP Information ────────────────────────────────────────────────────
        elapsed = ""
        if bridge.connected_at:
            el = int((datetime.now() - bridge.connected_at).total_seconds())
            m, s = divmod(el, 60)
            elapsed = f"{m:02d}:{s:02d}"

        st.markdown('<div class="p3-section-title">CHARGE POINT INFORMATION</div>', unsafe_allow_html=True)
        conn_rows = []
        for c in all_cids:
            cs   = bridge.connector_status(c)
            ctxn = bridge.active_txn(c)
            cwh  = bridge.meter_wh(c)
            csoc = bridge.soc_pct(c)
            bg   = "background:rgba(45,127,255,0.06);" if c==cid else ""
            txn_info = (
                f"<span style=\'color:var(--p3-teal);font-size:9px;\'>tx#{ctxn}&nbsp;{cwh:.0f}Wh&nbsp;SoC&nbsp;{csoc:.0f}%</span>"
                if ctxn else "<span style=\'color:var(--p3-dim);font-size:9px;\'>—</span>"
            )
            conn_rows.append(
                f'<div class="p3-info-row" style="{bg}"><span class="p3-info-key">CON {c}</span><span class="p3-info-val" style="display:flex;gap:6px;align-items:center;">{connector_badge(cs)}&nbsp;{txn_info}</span></div>'
            )
        st.markdown(
            f'<div class="p3-info-row"><span class="p3-info-key">CP ID</span><span class="p3-info-val">{bridge.cp_id}</span></div>'
            f'<div class="p3-info-row"><span class="p3-info-key">Vendor / Model</span><span class="p3-info-val">{bridge.cp_vendor} / {bridge.cp_model}</span></div>'
            f'<div class="p3-info-row"><span class="p3-info-key">CSMS</span><span class="p3-info-val" style="font-size:9px;">{bridge.csms_url}</span></div>'
            f'<div class="p3-info-row"><span class="p3-info-key">Connection</span><span class="p3-info-val">{badge_html(bridge.status)}</span></div>'
            f'<div class="p3-info-row"><span class="p3-info-key">Uptime</span><span class="p3-info-val">{elapsed or "—"}</span></div>'
            + "".join(conn_rows),
            unsafe_allow_html=True,
        )

        st.markdown("---")

        # ── Meter values form ─────────────────────────────────────────────────
        st.markdown('<div class="p3-section-title">SEND METER VALUES</div>', unsafe_allow_html=True)
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            mv_e = st.number_input("Energy Wh",    min_value=0.0, value=float(bridge.meter_wh(cid)), step=100.0, key="mv_e")
            mv_v = st.number_input("Voltage V",    min_value=0.0, value=220.0, step=1.0, key="mv_v")
        with mc2:
            mv_p = st.number_input("Power W",      min_value=0.0, value=7000.0, step=100.0, key="mv_p")
            mv_c = st.number_input("Current A",    min_value=0.0, value=31.8, step=0.1, key="mv_c")
        with mc3:
            mv_t = st.number_input("Temp °C",      min_value=0.0, max_value=100.0, value=35.0, step=0.5, key="mv_t")
            mv_s = st.number_input("SoC %",        min_value=0.0, max_value=100.0, value=50.0, step=1.0, key="mv_s")
        if st.button("📡  SEND METER VALUES", type="primary", use_container_width=True, disabled=not bridge.is_connected, key="btn_mv"):
            bridge.do_meter_values(cid, mv_e, mv_p, mv_v, mv_c, mv_t, mv_s)
            st.success(f"✓ MeterValues — {mv_t:.1f}°C · SoC {mv_s:.0f}%")

    # ════════════════════════════════════════════
    # RIGHT PANEL
    # ════════════════════════════════════════════
    with right_col:

        if ov is None:
            # ── Charge animation or SoC loading screen ────────────────────────
            is_charging = (cstat == "Charging")
            soc  = bridge.soc_pct(cid)
            temp = bridge.temperature_c(cid)
            txn  = bridge.active_txn(cid)

            if is_charging and txn:
                soc_fill  = max(0.0, min(100.0, soc))
                soc_color = "#ff3355" if soc_fill<20 else "#ffe066" if soc_fill<50 else "#2d7fff" if soc_fill<80 else "#2dff8f"
                gif_left  = max(4, min(88, soc_fill - 6))
                gif_tag   = f'<img src="{GIF_SRC}" style="height:80px;" alt="Aigis"/>' if GIF_SRC else '<div style="height:80px;"></div>'
                temp_color = "var(--p3-red)" if temp>60 else "var(--p3-yellow)" if temp>45 else "var(--p3-teal)"
                st.markdown(f"""
                <div style="padding:8px 0;">
                  <div style="position:relative;height:86px;margin-bottom:4px;">
                    <div style="position:absolute;left:{gif_left:.1f}%;bottom:0;transform:translateX(-50%);transition:left 1s ease;filter:drop-shadow(0 0 8px {soc_color});">{gif_tag}</div>
                  </div>
                  <div style="display:flex;justify-content:space-between;font-family:\'Share Tech Mono\',monospace;font-size:9px;color:var(--p3-dim);margin-bottom:4px;letter-spacing:1px;">
                    <span>STATE OF CHARGE</span>
                    <span style="color:{soc_color};font-size:12px;font-weight:700;">{soc_fill:.1f}%</span>
                  </div>
                  <div style="width:100%;height:12px;background:rgba(45,127,255,0.08);border:1px solid rgba(45,127,255,0.2);border-radius:6px;overflow:hidden;position:relative;">
                    <div style="height:100%;width:{soc_fill:.1f}%;background:linear-gradient(90deg,#1a50c8,{soc_color});border-radius:6px;position:relative;">
                      <div style="position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.2),transparent);animation:shimmer 1.8s ease-in-out infinite;border-radius:6px;"></div>
                    </div>
                    <div style="position:absolute;inset:0;display:flex;pointer-events:none;">
                      <div style="flex:1;border-right:1px solid rgba(255,255,255,0.08)"></div>
                      <div style="flex:1;border-right:1px solid rgba(255,255,255,0.08)"></div>
                      <div style="flex:1;border-right:1px solid rgba(255,255,255,0.08)"></div>
                      <div style="flex:1;"></div>
                    </div>
                  </div>
                  <div style="display:flex;justify-content:space-between;font-family:\'Share Tech Mono\',monospace;font-size:8px;color:rgba(110,127,160,0.45);margin-top:2px;">
                    <span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span>
                  </div>
                  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px;margin-top:8px;">
                    <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:5px 7px;">
                      <div style="font-family:\'Share Tech Mono\',monospace;font-size:8px;color:var(--p3-dim);text-transform:uppercase;letter-spacing:1px;">Energy</div>
                      <div style="font-family:\'Orbitron\',sans-serif;font-size:12px;color:var(--p3-teal);font-weight:700;">{bridge.meter_wh(cid):.0f}<span style="font-size:8px;color:var(--p3-dim);margin-left:2px;">Wh</span></div>
                    </div>
                    <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:5px 7px;">
                      <div style="font-family:\'Share Tech Mono\',monospace;font-size:8px;color:var(--p3-dim);text-transform:uppercase;letter-spacing:1px;">Temp</div>
                      <div style="font-family:\'Orbitron\',sans-serif;font-size:12px;color:{temp_color};font-weight:700;">{temp:.1f}<span style="font-size:8px;color:var(--p3-dim);margin-left:2px;">°C</span></div>
                    </div>
                    <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:5px 7px;">
                      <div style="font-family:\'Share Tech Mono\',monospace;font-size:8px;color:var(--p3-dim);text-transform:uppercase;letter-spacing:1px;">TX</div>
                      <div style="font-family:\'Orbitron\',sans-serif;font-size:12px;color:var(--p3-white);font-weight:700;">#{txn}</div>
                    </div>
                  </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(
                    f'<div class="p3-charge-anim"><div class="p3-bolt idle">⚡</div><div class="p3-status-text">{cstat.upper()}</div></div>',
                    unsafe_allow_html=True,
                )

            # ── Real-time chart ───────────────────────────────────────────────
            pts = bridge.chart_points(cid)
            if pts:
                df = pd.DataFrame(pts)
                st.markdown(
                    '<div style="font-family:\'Orbitron\',sans-serif;font-size:8px;'
                    'letter-spacing:2px;color:var(--p3-dim);margin:6px 0 2px;">'
                    '📊 REAL-TIME CHART</div>',
                    unsafe_allow_html=True,
                )
                tab_chart = st.selectbox(
                    "Metric",
                    ["SoC (%)", "Power (W)", "Energy (Wh)", "Temp (°C)"],
                    key="chart_metric",
                    label_visibility="collapsed",
                )
                col_map = {
                    "SoC (%)":    "soc",
                    "Power (W)":  "power",
                    "Energy (Wh)": "energy",
                    "Temp (°C)":  "temp",
                }
                chart_col = col_map[tab_chart]
                chart_df  = df[["ts", chart_col]].rename(columns={"ts": "Time", chart_col: tab_chart})
                chart_df  = chart_df.set_index("Time")
                st.line_chart(
                    chart_df,
                    height=120,
                    use_container_width=True,
                )

            st.markdown("---")
            id_tag_q = st.session_state.get("id_tag_quick", "A56DEF4B")
            id_tag_q = st.text_input("ID TAG", value=id_tag_q, key="id_tag_quick_input")
            st.session_state["id_tag_quick"] = id_tag_q
            if bridge.active_txn(cid) is None:
                if st.button("▶  START TRANSACTION", type="primary", use_container_width=True, disabled=not bridge.is_connected, key="btn_start_q"):
                    bridge.do_status_notification(cid, "Preparing")
                    bridge.do_start_transaction(cid, id_tag_q)
                    time.sleep(0.3)
                    st.rerun()
            else:
                if st.button("■  STOP TRANSACTION", type="primary", use_container_width=True, disabled=not bridge.is_connected, key="btn_stop_q"):
                    bridge.do_stop_transaction(cid, id_tag_q)
                    time.sleep(0.3)
                    st.rerun()

        elif ov == "authorize":
            st.markdown('<div class="p3-overlay"><div class="p3-overlay-title">AUTHORIZE</div>', unsafe_allow_html=True)
            rfid = st.text_input("RFID / ID TAG", value="A56DEF4B", key="auth_rfid")
            if st.button("🔑  AUTHORIZE", type="primary", use_container_width=True, disabled=not bridge.is_connected, key="btn_auth_do"):
                bridge.do_authorize(rfid)
                st.success("✓ Authorize sent")
            st.markdown("</div>", unsafe_allow_html=True)

        elif ov == "logs":
            st.markdown('<div class="p3-overlay"><div class="p3-overlay-title">MESSAGE LOG</div>', unsafe_allow_html=True)
            logs = bridge.logs
            sent = sum(1 for l in logs if l.direction=="out")
            recv = sum(1 for l in logs if l.direction=="in")
            lc1, lc2 = st.columns([3,1])
            with lc1:
                st.markdown(f'<div style="font-family:\'Share Tech Mono\',monospace;font-size:9px;color:var(--p3-dim);">▲ {sent} sent &nbsp; ▼ {recv} recv &nbsp; · {len(logs)} total</div>', unsafe_allow_html=True)
            with lc2:
                if st.button("CLR", key="btn_clr_log", type="secondary"):
                    bridge.clear_logs(); st.rerun()
            render_log_entries()
            st.markdown("</div>", unsafe_allow_html=True)

        elif ov == "test":
            st.markdown(f'<div class="p3-overlay"><div class="p3-overlay-title">STATUS TEST</div><div style="font-family:\'Share Tech Mono\',monospace;font-size:9px;color:var(--p3-dim);margin-bottom:8px;">CON {cid} current: {connector_badge(cstat)}</div>', unsafe_allow_html=True)
            sel_st = st.radio("status", ["Available","Preparing","Charging","SuspendedEVSE","SuspendedEV","Finishing","Reserved","Unavailable","Faulted"], horizontal=True, key="test_st_radio", label_visibility="collapsed")
            if st.button("📤  SEND STATUS", type="primary", use_container_width=True, disabled=not bridge.is_connected, key="btn_send_st"):
                bridge.do_status_notification(cid, sel_st)
                st.success(f"✓ StatusNotification '{sel_st}' sent")
            st.markdown("---")
            st.markdown('<div style="font-family:\'Orbitron\',sans-serif;font-size:8px;letter-spacing:2px;color:var(--p3-dim);margin-bottom:6px;">QUICK ACTIONS</div>', unsafe_allow_html=True)
            qa1, qa2 = st.columns(2)
            with qa1:
                if st.button("HEARTBEAT", use_container_width=True, type="secondary", disabled=not bridge.is_connected, key="btn_hb"):
                    bridge.do_heartbeat(); st.success("✓ Heartbeat")
            with qa2:
                if st.button("BOOT NOTIF", use_container_width=True, type="secondary", disabled=not bridge.is_connected, key="btn_boot"):
                    bridge.do_boot_notification(); st.success("✓ BootNotification")
            st.markdown("</div>", unsafe_allow_html=True)

        elif ov == "history":
            # ── Session history overlay ───────────────────────────────────────
            st.markdown('<div class="p3-overlay"><div class="p3-overlay-title">📋 RIWAYAT SESI</div>', unsafe_allow_html=True)
            sessions = bridge.session_history

            if not sessions:
                st.markdown(
                    '<div style="font-family:\'Share Tech Mono\',monospace;font-size:10px;'
                    'color:var(--p3-dim);text-align:center;padding:20px 0;">'
                    '— belum ada riwayat sesi —</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="font-family:\'Share Tech Mono\',monospace;font-size:9px;'
                    f'color:var(--p3-dim);margin-bottom:8px;">{len(sessions)} sesi tersimpan (7 hari terakhir)</div>',
                    unsafe_allow_html=True,
                )
                # Detail sesi yang dipilih
                sel_idx = st.selectbox(
                    "Pilih sesi",
                    range(len(sessions)),
                    format_func=lambda i: (
                        f"tx#{sessions[i].get('transaction_id')} | "
                        f"{sessions[i].get('start_time','')[:16].replace('T',' ')} | "
                        f"CON {sessions[i].get('connector_id')} | "
                        f"{sessions[i].get('energy_wh',0):.0f}Wh"
                    ),
                    key="hist_sel",
                    label_visibility="collapsed",
                )
                s = sessions[sel_idx]
                dur_m, dur_s = divmod(s.get("duration_s", 0), 60)
                st.markdown(f"""
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;
                  font-family:\'Share Tech Mono\',monospace;font-size:9px;margin-bottom:8px;">
                  <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:5px 8px;">
                    <div style="color:var(--p3-dim);">TX ID</div>
                    <div style="color:var(--p3-white);font-size:11px;font-weight:700;">#{s.get("transaction_id")}</div>
                  </div>
                  <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:5px 8px;">
                    <div style="color:var(--p3-dim);">ID TAG</div>
                    <div style="color:var(--p3-white);font-size:11px;font-weight:700;">{s.get("id_tag","—")}</div>
                  </div>
                  <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:5px 8px;">
                    <div style="color:var(--p3-dim);">DURASI</div>
                    <div style="color:var(--p3-teal);font-size:11px;font-weight:700;">{dur_m:02d}:{dur_s:02d}</div>
                  </div>
                  <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:5px 8px;">
                    <div style="color:var(--p3-dim);">ENERGI</div>
                    <div style="color:var(--p3-teal);font-size:11px;font-weight:700;">{s.get("energy_wh",0):.1f} Wh</div>
                  </div>
                  <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:5px 8px;">
                    <div style="color:var(--p3-dim);">SOC</div>
                    <div style="color:var(--p3-bright);font-size:11px;font-weight:700;">{s.get("soc_start",0):.0f}% → {s.get("soc_end",0):.0f}%</div>
                  </div>
                  <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:5px 8px;">
                    <div style="color:var(--p3-dim);">MAX TEMP</div>
                    <div style="color:{"var(--p3-red)" if s.get("max_temp_c",0)>50 else "var(--p3-yellow)" if s.get("max_temp_c",0)>40 else "var(--p3-teal)"};font-size:11px;font-weight:700;">{s.get("max_temp_c",0):.1f} °C</div>
                  </div>
                </div>
                """, unsafe_allow_html=True)

                # Grafik sesi historis
                pts = s.get("chart_points", [])
                if pts:
                    df_h = pd.DataFrame(pts)
                    hist_metric = st.selectbox(
                        "Metric grafik",
                        ["SoC (%)", "Power (W)", "Energy (Wh)", "Temp (°C)"],
                        key="hist_chart_metric",
                        label_visibility="collapsed",
                    )
                    col_map = {"SoC (%)": "soc", "Power (W)": "power",
                               "Energy (Wh)": "energy", "Temp (°C)": "temp"}
                    hcol  = col_map[hist_metric]
                    df_hc = df_h[["ts", hcol]].rename(columns={"ts": "Time", hcol: hist_metric}).set_index("Time")
                    st.line_chart(df_hc, height=100, use_container_width=True)

            st.markdown("</div>", unsafe_allow_html=True)

        elif ov == "ev_sim":
            # ── EV Simulator overlay ──────────────────────────────────────────
            sim = get_active_sim(connector_id)
            is_running = sim is not None and not sim._stop

            st.markdown('<div class="p3-overlay"><div class="p3-overlay-title">🚗 EV SIMULATOR</div>', unsafe_allow_html=True)

            if is_running:
                ev = sim.ev
                soc     = ev.soc_pct
                target  = ev.soc_target
                elapsed = ""
                if ev.session_start:
                    s = int((datetime.now() - ev.session_start).total_seconds())
                    elapsed = f"{s//60:02d}:{s%60:02d}"

                filled = int((soc / 100) * 28)
                bar    = "█" * filled + "░" * (28 - filled)
                soc_color = "#ff3355" if soc<20 else "#ffe066" if soc<50 else "#2d7fff" if soc<80 else "#2dff8f"

                st.markdown(f'''
                <div style="font-family:\'Share Tech Mono\',monospace;font-size:10px;color:var(--p3-dim);margin-bottom:10px;">
                  <span style="color:var(--p3-green);">● BERJALAN</span>
                  &nbsp;&nbsp;CON {ev.connector_id}
                  &nbsp;&nbsp;ID: {ev.id_tag}
                  &nbsp;&nbsp;⏱ {elapsed}
                </div>
                <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--p3-dim);margin-bottom:3px;">
                  <span>STATE OF CHARGE</span>
                  <span style="color:{soc_color};font-weight:700;">{soc:.1f}% → {target:.0f}%</span>
                </div>
                <div style="font-family:\'Share Tech Mono\',monospace;font-size:11px;color:{soc_color};margin-bottom:6px;">
                  [{bar}]
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px;font-family:\'Share Tech Mono\',monospace;font-size:9px;">
                  <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:4px 6px;">
                    <div style="color:var(--p3-dim);">ENERGY</div>
                    <div style="color:var(--p3-teal);font-size:11px;font-weight:700;">{ev.energy_delivered:.0f} Wh</div>
                  </div>
                  <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:4px 6px;">
                    <div style="color:var(--p3-dim);">POWER</div>
                    <div style="color:var(--p3-teal);font-size:11px;font-weight:700;">{ev.effective_power/1000:.1f} kW</div>
                  </div>
                  <div style="background:rgba(10,31,78,0.5);border:1px solid rgba(45,127,255,0.15);border-radius:3px;padding:4px 6px;">
                    <div style="color:var(--p3-dim);">TEMP</div>
                    <div style="color:{"var(--p3-red)" if ev.temperature_c>50 else "var(--p3-yellow)" if ev.temperature_c>40 else "var(--p3-teal)"};font-size:11px;font-weight:700;">{ev.temperature_c:.1f} °C</div>
                  </div>
                </div>
                ''', unsafe_allow_html=True)

                st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
                if st.button("⏹  STOP EV", type="primary", use_container_width=True, key="btn_ev_stop"):
                    stop_ev_sim()
                    st.rerun()

            else:
                # EV config form
                st.markdown('<div style="font-family:\'Share Tech Mono\',monospace;font-size:9px;color:var(--p3-dim);margin-bottom:10px;">Konfigurasi kendaraan yang akan disimulasikan</div>', unsafe_allow_html=True)

                ev_c1, ev_c2 = st.columns(2)
                with ev_c1:
                    ev_id_tag  = st.text_input("ID Tag / RFID",      value=DEFAULT_ID_TAG,      key="ev_id_tag")
                    ev_soc_s   = st.number_input("SoC Awal (%)",     min_value=0.0, max_value=99.0,  value=20.0, step=5.0,  key="ev_soc_s")
                    ev_batt    = st.number_input("Baterai (kWh)",     min_value=1.0, max_value=200.0, value=DEFAULT_BATTERY_KWH, step=5.0, key="ev_batt")
                with ev_c2:
                    ev_con     = st.selectbox("Connector", all_cids, index=0, key="ev_con")
                    ev_soc_t   = st.number_input("SoC Target (%)",   min_value=1.0,  max_value=100.0, value=80.0, step=5.0,  key="ev_soc_t")
                    ev_power   = st.number_input("Max Power (W)",    min_value=1000.0, max_value=150000.0, value=DEFAULT_MAX_POWER_W, step=500.0, key="ev_power")

                ev_interval = st.select_slider(
                    "Interval MeterValues (detik)",
                    options=[1, 2,  5, 10, 15, 30, 60],
                    value=DEFAULT_METER_INTERVAL_S,
                    key="ev_interval"
                )

                st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
                if st.button("▶  COLOK KABEL & MULAI", type="primary",
                             use_container_width=True,
                             disabled=not bridge.is_connected,
                             key="btn_ev_start"):
                    start_ev_sim(
                        connector_id     = ev_con,
                        id_tag           = ev_id_tag,
                        soc_start        = ev_soc_s,
                        soc_target       = ev_soc_t,
                        battery_kwh      = ev_batt,
                        max_power_w      = ev_power,
                        meter_interval_s = ev_interval,
                    )
                    st.rerun()

            st.markdown("</div>", unsafe_allow_html=True)

        elif ov == "notifications":
            st.markdown('<div class="p3-overlay"><div class="p3-overlay-title">🔔 REMOTE COMMANDS</div>', unsafe_allow_html=True)
            notifs = bridge.notifications
            if not notifs:
                st.markdown('<div style="font-family:\'Share Tech Mono\',monospace;font-size:10px;color:var(--p3-dim);text-align:center;padding:20px 0;">— no remote commands —</div>', unsafe_allow_html=True)
            else:
                kind_colors = {"remote_start":"var(--p3-bright)","remote_stop":"var(--p3-red)","change_avail":"var(--p3-yellow)","reset":"var(--p3-teal)"}
                rows = ['<div class="p3-log-wrap">']
                for n in notifs:
                    ts    = n.timestamp.strftime("%H:%M:%S")
                    color = kind_colors.get(n.kind, "var(--p3-dim)")
                    p     = n.payload
                    if n.kind=="remote_start":   kvs=[("CON",f"CON {p.get('connector_id','?')}"),("Tag",p.get("id_tag","?"))]
                    elif n.kind=="remote_stop":  kvs=[("TX",f"#{p.get('transaction_id','?')}"),("CON",f"CON {p.get('connector_id','?')}")] 
                    elif n.kind=="change_avail": kvs=[("CON","ALL" if not p.get("connector_id") else f"CON {p.get('connector_id')}"),("State",p.get("status","?"))]
                    elif n.kind=="reset":        kvs=[("Type",p.get("type","?"))]
                    else:                        kvs=[(k,str(v)) for k,v in p.items()]
                    kv = " ".join(f'<span style="font-size:9px;color:var(--p3-dim);margin-right:8px;">{k}: <b style="color:var(--p3-white);">{v}</b></span>' for k,v in kvs)
                    unrd = "p3-notif-unread" if not n.read else ""
                    rows.append(f'<div class="p3-notif {unrd}"><div style="display:flex;justify-content:space-between;"><span style="font-family:\'Orbitron\',sans-serif;font-size:9px;font-weight:700;color:{color};">{n.icon} {n.label.upper()}</span><span style="font-size:8px;color:rgba(110,127,160,0.5);">{ts}</span></div><div style="margin-top:3px;">{kv}</div></div>')
                rows.append("</div>")
                st.markdown("".join(rows), unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

    # ── Auto refresh ───────────────────────────────────────────────────────────
    time.sleep(2)
    st.rerun()

    st.rerun()
