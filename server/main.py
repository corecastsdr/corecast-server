#
# Core Cast Server - Main Application
#
# This Python script uses GNURadio and WebSockets to create the Core Cast
# multi-user SDR server. It performs the following functions:
#
# 1. Connects to a remote SoapySDR device (via `soapy_server`).
# 2. Performs robust sample rate negotiation to find a stable rate.
# 3. Creates a main `CoreSDR` (gr.top_block) to:
#    - Receive the wideband I/Q stream.
#    - Generate FFT data for the waterfall.
#    - Publish the I/Q stream via ZeroMQ (ZMQ) for fanning out.
# 4. Spawns a `ClientRx` (gr.top_block) for *each* connected user, which:
#    - Subscribes to the ZMQ I/Q stream.
#    - Performs DSP (mixing, filtering, demodulation) per user's request.
# 5. Runs two WebSocket servers:
#    - Port 3050: Audio server (sends audio, receives 'tune' commands).
#    - Port 3051: Waterfall server (sends FFT data, receives 'span' commands).
#
# 6. (NEW) Securely logs verified listener session duration to a private
#    metrics API if INTERNAL_API_KEY is provided.
#
# Configuration is handled via environment variables.
#

import importlib, sys, time, os, random, asyncio, json, signal, itertools
import numpy as np, websockets
import httpx # <-- 1. NEW IMPORT (run 'pip install httpx')

# ---- GR compat shim ----
try:
    import gnuradio.gr.hier_block2 as _hb2
    import gnuradio.gr as _gr
    if not hasattr(_gr, "hier_block2"): _gr.hier_block2 = _hb2.hier_block2
    if not hasattr(_gr, "top_block"): _gr.top_block = importlib.import_module("gnuradio.gr.top_block_pb").top_block
except Exception as e:
    print("⚠  GNURadio patch failed:", e, flush=True)

from gnuradio import gr, blocks, filter, analog, soapy, fft, zeromq
from gnuradio.filter import firdes
from gnuradio.fft   import window
import SoapySDR as s
from SoapySDR import SOAPY_SDR_RX

# ───────── logging helpers ─────────
def ts(): return time.strftime("%Y-%m-%d %H:%M:%S")
def log(*a): print(f"[{ts()}]", *a, flush=True)
def jlog(tag, **k): print(json.dumps({"ts": ts(), "tag": tag, **k}, ensure_ascii=False), flush=True)

# ───────── env helpers ─────────
def env_int(name, default): return int(os.getenv(name, default))
def env_float(name, default): return float(os.getenv(name, default))

# ───────── RF params ─────────
CENTER     = env_float("CENTER",     104.10e6)
SPAN_RATE  = env_int  ("SPAN_RATE",  2_048_000)
FFT_SIZE   = env_int  ("FFT_SIZE",   2048)
AUD_RATE   = env_int  ("AUD_RATE",   48_000)
GAIN_DB    = env_float("GAIN_DB",    20.0)
CHUNK_MS   = env_int  ("CHUNK_MS",   20)

# --- Soapy Remote Config ---
REMOTE_URL   = os.getenv("SDR_REMOTE", "127.0.0.1:6000")
REMOTE_PROT  = os.getenv("REMOTE_PROT", "tcp")
REMOTE_TO_MS = env_int("REMOTE_TIMEOUT_MS", 8000)
REMOTE_MTU   = os.getenv("REMOTE_MTU")

# --- 2. NEW: Metrics API Configuration ---
API_KEY = os.getenv("INTERNAL_API_KEY")
API_URL = os.getenv("METRICS_API_URL") # e.g., "http://corecast-api:7000/api/metrics"
http_client = None

if API_KEY and API_URL:
    log("✅ Metrics API enabled.")
    http_client = httpx.Client(
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        timeout=5.0
    )
else:
    log("⚠️  Metrics API key/URL not set. Skipping stats logging. (This is safe for open-source builds)")

# ==============================================================================
# === THE CORRECT, ROBUST WAY TO BUILD THE DEVICE STRING ===
# ==============================================================================
dev_parts = [
    f"driver=remote",
    f"remote=tcp://{REMOTE_URL}",
    f"remote:prot={REMOTE_PROT}",
    f"remote:timeout={REMOTE_TO_MS}",
]
if REMOTE_MTU:
    dev_parts.append(f"remote:mtu={REMOTE_MTU}")

DEV_STR = ",".join(dev_parts)
log(f"DEBUG: Attempting to connect with device string: {DEV_STR}")
# ==============================================================================

# --- DSP/App Globals ---
ZMQ_ADDR = f"tcp://127.0.0.1:{random.randint(5600, 5900)}"
LOW_HZ   = CENTER - SPAN_RATE/2
HIGH_HZ  = CENTER + SPAN_RATE/2
BIN_HZ   = SPAN_RATE / FFT_SIZE
CHUNK_S  = AUD_RATE * CHUNK_MS // 1000

# ... (All functions from hz_to_bin to pick_working_rate are UNCHANGED) ...
def hz_to_bin(hz: float) -> int:
    return int(max(0, min(FFT_SIZE-1, round((hz-LOW_HZ)/BIN_HZ))))
def _open_device_for_probe():
    dev = s.Device(DEV_STR)
    info = dev.getHardwareInfo()
    caps = {"hardwareKey": dev.getHardwareKey(), "info": {k: str(v) for k,v in info.items()}}
    try:
        rngs = dev.getSampleRateRange(SOAPY_SDR_RX, 0); caps["rate_ranges"] = [(int(r.minimum()), int(r.maximum())) for r in rngs]
    except Exception as e: caps["rate_ranges_err"] = str(e)
    try:
        lst = dev.listSampleRates(SOAPY_SDR_RX, 0); caps["rate_list"] = [int(x) for x in lst] if lst else []
    except Exception as e: caps["rate_list_err"] = str(e)
    return dev, caps
def _try_dev_setrate(dev, val:int) -> bool:
    try:
        dev.setFrequency(SOAPY_SDR_RX, 0, CENTER); dev.setGain(SOAPY_SDR_RX, 0, "TUNER", GAIN_DB); time.sleep(0.05)
        dev.setSampleRate(SOAPY_SDR_RX, 0, val)
        got = int(dev.getSampleRate(SOAPY_SDR_RX, 0))
        log(f"[probe] (Device) ✓ setSampleRate({val}) -> {got}"); return True
    except Exception as e:
        log(f"[probe] (Device) ✗ setSampleRate({val}) -> {e}"); time.sleep(0.05); return False
def _try_block_setrate(val:int) -> bool:
    try:
        src = soapy.source(DEV_STR, "fc32", 1, "", "bufflen=16384", [""], [""])
        src.set_frequency(0, CENTER); src.set_gain(0, "TUNER", GAIN_DB); time.sleep(0.05)
        src.set_sample_rate(0, val)
        log(f"[probe] (GR block) ✓ set_sample_rate({val})"); return True
    except Exception as e:
        log(f"[probe] (GR block) ✗ set_sample_rate({val}) -> {e}"); time.sleep(0.05); return False
def pick_working_rate(target:int) -> int:
    log(f"[probe] opening {REMOTE_URL} (prot={REMOTE_PROT}, timeout={REMOTE_TO_MS}ms)")
    dev, caps = _open_device_for_probe()
    jlog("remote_caps", remote=REMOTE_URL, **caps)
    cands = [target, 2_048_000, 2_000_000, 1_920_000, 1_792_000, 1_536_000, 1_280_000, 1_024_000, 900_001, 2_560_000, 2_880_000, 3_200_000]
    if "rate_ranges" in caps: cands = [c for c in cands if any(lo <= c <= hi for lo,hi in caps["rate_ranges"])]
    for c in cands:
        if _try_dev_setrate(dev, c): return c
    for c in cands:
        if _try_block_setrate(c): return c
    raise RuntimeError("No acceptable sample rate found")
# ... (CoreSDR class is UNCHANGED) ...
class CoreSDR(gr.top_block):
    def __init__(self):
        super().__init__("core")
        chosen = pick_working_rate(SPAN_RATE)
        jlog("rate_selected", remote=REMOTE_URL, target=SPAN_RATE, chosen=chosen)
        src = soapy.source(DEV_STR, "fc32", 1, "", "bufflen=16384", [""], [""])
        for attempt in (chosen, 2_048_000, 1_920_000, 1_536_000, 1_024_000):
            try: src.set_sample_rate(0, attempt); log(f"[gr] src.set_sample_rate -> {attempt}"); break
            except Exception as e: log(f"[gr] set_sample_rate({attempt}) failed: {e}")
        else: raise RuntimeError("GNURadio soapy.source refused every tested rate")
        src.set_frequency(0, CENTER); src.set_gain(0, "TUNER", GAIN_DB)
        pub = zeromq.pub_sink(gr.sizeof_gr_complex, 1, ZMQ_ADDR, 100, False, -1)
        vec = blocks.stream_to_vector(gr.sizeof_gr_complex, FFT_SIZE)
        fftb = fft.fft_vcc(FFT_SIZE, True, window.blackmanharris(FFT_SIZE), True, 3)
        mag2 = blocks.complex_to_mag_squared(FFT_SIZE)
        logp = blocks.nlog10_ff(10, FFT_SIZE, 1e-10)
        self._sink = blocks.vector_sink_f(vlen=FFT_SIZE)
        self.connect(src, pub)
        self.connect(src, vec, fftb, mag2, logp, self._sink)
        self.start()
    def grab_fft(self):
        data = self._sink.data()
        if not data: return None
        frame = list(data[:FFT_SIZE]); self._sink.reset(); return frame
# ... (ClientRx class is UNCHANGED) ...
class ClientRx(gr.top_block):
    _ids = itertools.count(1)
    def __init__(self, off_hz, mode, bw, sql):
        super().__init__(f"Rx#{next(self._ids)}")
        self.off, self.mode, self.bw, self.sql = off_hz, (mode or "wbfm").lower(), bw, sql
        self._build(); self.start()
    def _build(self):
        self.lock(); self.disconnect_all()
        zmq = zeromq.sub_source(gr.sizeof_gr_complex, 1, ZMQ_ADDR, 100, False, True)
        bw = max(10e3, min(180e3, self.bw or 200e3))
        taps = firdes.low_pass(1, SPAN_RATE, bw/2, bw/4)
        dec = SPAN_RATE // 8
        mix = filter.freq_xlating_fir_filter_ccf(8, taps, -self.off, SPAN_RATE)
        m = self.mode
        if m=="wbfm": demod = analog.wfm_rcv(quad_rate=dec, audio_decimation=int(dec//AUD_RATE))
        elif m=="nbfm": demod = analog.nbfm_rx(audio_rate=AUD_RATE, quad_rate=dec, tau=750e-6, max_dev=bw/4)
        elif m=="am": demod = analog.am_demod_cf(dec, int(dec//AUD_RATE), bw/4, bw/2)
        elif m in ("usb","lsb"): demod = analog.ssbdemod_cf(dec, AUD_RATE, bw/4, 1 if m=="usb" else 0)
        else: raise ValueError("bad mode")
        if self.sql is not None:
            squelch = analog.pwr_squelch_ff(self.sql, 1e-3, 0, True)
            self.connect(zmq, mix, demod, squelch); tail = squelch
        else:
            self.connect(zmq, mix, demod); tail = demod
        self.snk = blocks.vector_sink_f()
        self.connect(tail, self.snk); self.unlock()
    def retune(self,*,off_hz=None,mode=None,bw=None,sql=None):
        if off_hz is not None: self.off=off_hz
        if mode is not None: self.mode=mode.lower()
        if bw is not None: self.bw=bw
        if sql is not None: self.sql=sql
        self._build()
    def pull(self):
        pcm = np.array(self.snk.data(), np.float32); self.snk.reset(); return pcm.tobytes()
    def close(self):
        self.stop(); self.wait()

class AudioSession:
    """
    Manages the WebSocket lifecycle for a single *audio* client.
    Contains the async loops for pumping audio and receiving tune commands.
    """
    # --- 3. MODIFY __init__ ---
    def __init__(self, ws, first, user_uuid, station_uuid):
        """Links the WebSocket to a new ClientRx flowgraph."""
        log(f"New audio session for user {user_uuid} on station {station_uuid}")
        self.ws = ws
        # Store identifiers for logging
        self.user_uuid = user_uuid
        self.station_uuid = station_uuid
        self.start_time = time.time()
        self.session_db_id = None # Will be set by log_session_start
        self.client_ip = ws.remote_address[0] if ws.remote_address else "unknown"

        # Create a dedicated DSP block for this client
        self.rx = ClientRx(first["freq"]-CENTER, first.get("mode","wbfm"), first.get("bw"), first.get("sql"))

    # --- 4. ADD HELPER METHODS for logging ---
    def log_session_start(self):
        """Calls the secure metrics API to log the start of a session."""
        # http_client is the global client
        if not http_client or not self.user_uuid or not self.station_uuid:
            if not http_client:
                log("Metrics API client not configured. Skipping stats.")
            else:
                log(f"Session start aborted: missing user_uuid ({self.user_uuid}) or station_uuid ({self.station_uuid})")
            return

        payload = {
            "user_uuid": self.user_uuid,
            "station_uuid": self.station_uuid,
            "client_ip": self.client_ip,
            "container_id": os.getenv("HOSTNAME", "unknown_sdr_server"),
            "freq_hz": self.rx.off + CENTER,
            "mode": self.rx.mode,
            "bw": self.rx.bw,
            "sql_level": self.rx.sql,
        }
        try:
            res = http_client.post(f"{API_URL}/session/start", json=payload)
            if res.status_code == 200:
                self.session_db_id = res.json().get("session_id")
                log(f"Logged session start for user {self.user_uuid}, db_id: {self.session_db_id}")
            else:
                log(f"API Error (start): {res.status_code} - {res.text}")
        except Exception as e:
            log(f"API Exception (start): {e}")

    def log_session_end(self):
        """Calls the secure metrics API to log the end of a session."""
        if not http_client or not self.session_db_id:
            if not http_client:
                log("Metrics API client not configured. Skipping stats.")
            else:
                log(f"Session end aborted for user {self.user_uuid}: no session_db_id (start log likely failed)")
            return

        # Calculate duration on the server. This is the *secure* part.
        duration = time.time() - self.start_time

        payload = {
            "session_db_id": self.session_db_id,
            # The Python API will use this field.
            "duration_sec": duration
        }
        try:
            # Send the request
            res = http_client.post(f"{API_URL}/session/end", json=payload)
            log(f"Logged session end for user {self.user_uuid}, db_id: {self.session_db_id}, duration: {duration:.2f}s. Status: {res.status_code}")
        except Exception as e:
            log(f"API Exception (end): {e}")

    async def pump_audio(self):
        """Async loop: Continuously pulls audio from ClientRx and sends it over the WebSocket."""
        while True:
            data = self.rx.pull()
            if data:
                await self.ws.send(data)
            else:
                await asyncio.sleep(CHUNK_MS/2000) # Sleep for 1/2 chunk time

    async def ctl(self):
        """Async loop: Listens for JSON 'tune' commands from the client and retunes the ClientRx."""
        async for txt in self.ws:
            if not isinstance(txt,str): continue
            try: cmd=json.loads(txt)
            except: continue
            if cmd.get("type")!="tune": continue
            f=cmd.get("freq")
            if f is not None and abs(f-CENTER)>SPAN_RATE/2: continue # Out of bounds

            # We only care about the UUIDs on the *first* packet,
            # which is handled by audio_handler.
            # Here we just apply the DSP settings.
            self.rx.retune(off_hz=f-CENTER if f is not None else None,mode=cmd.get("mode"),bw=cmd.get("bw"),sql=cmd.get("sql"))
            jlog("tune", **cmd)

    async def run(self):
        """Runs the audio pump and control loops concurrently."""
        try:
            # --- 5. CALL THE LOG START METHOD ---
            self.log_session_start()
            await asyncio.gather(self.pump_audio(), self.ctl())
        finally:
            # --- 6. CALL THE LOG END METHOD ---
            # This executes when the WebSocket closes for any reason.
            self.log_session_end()
            self.rx.close() # Ensure flowgraph is stopped on exit

# ... (WfSession class is UNCHANGED) ...
class WfSession:
    def __init__(self, ws):
        self.ws = ws
        self.minH, self.maxH = LOW_HZ, HIGH_HZ
    def _slice(self):
        a,b=hz_to_bin(self.minH),hz_to_bin(self.maxH); return min(a,b),max(a,b)
    async def ctl(self):
        async for txt in self.ws:
            try: cmd=json.loads(txt)
            except: continue
            if cmd.get("type")!="span": continue
            lo,hi=max(LOW_HZ,min(HIGH_HZ,float(cmd.get("min",LOW_HZ)))),max(LOW_HZ,min(HIGH_HZ,float(cmd.get("max",HIGH_HZ))))
            if hi-lo>=BIN_HZ: self.minH,self.maxH=lo,hi
    async def pump(self, core):
        while True:
            line = core.grab_fft()
            if line:
                a,b=self._slice()
                await self.ws.send(json.dumps({"type":"waterfall","data":line[a:b]}))
            await asyncio.sleep(CHUNK_MS/1000)
    async def run(self, core):
        await asyncio.gather(self.pump(core),self.ctl())


async def audio_handler(ws, _p, core):
    """
    Main entrypoint for new audio WebSocket connections (port 3050).
    Validates the first packet and starts an AudioSession.
    """
    try:
        # Wait for the first "tune" packet
        first_packet_str = await ws.recv()
        first = json.loads(first_packet_str)

        if first.get("type") != "tune":
            await ws.close(4000, "need tune"); return
        if abs(first["freq"] - CENTER) > SPAN_RATE/2:
            await ws.close(4001, "out of span"); return

        # --- 7. EXTRACT UUIDs and PASS them ---
        user_uuid = first.get("user_uuid")
        station_uuid = first.get("station_uuid")

        if not user_uuid:
            # We require an authenticated user to log stats.
            log(f"Connection from {ws.remote_address[0]} rejected: user_uuid not provided in first tune packet.")
            await ws.close(4002, "user_uuid required"); return

        if not station_uuid:
            log(f"Connection from {user_uuid} rejected: station_uuid not provided in first tune packet.")
            await ws.close(4003, "station_uuid required"); return

        # Hand off to a new session, passing the validated data
        await AudioSession(ws, first, user_uuid, station_uuid).run()

    except websockets.ConnectionClosed:
        pass # Handle disconnects gracefully
    except Exception as e:
        log(f"Audio handler error: {e}. Packet: '{first_packet_str if 'first_packet_str' in locals() else 'N/A'}'")


async def wf_handler(ws, _p, core):
    """
    Main entrypoint for new waterfall WebSocket connections (port 3051).
    Starts a WfSession.
    """
    try: await WfSession(ws,).run(core)
    except websockets.ConnectionClosed: pass # Handle disconnects gracefully

# ==============================================================================
# === THE CORRECT, FINAL VERSION OF dump_env() ===
# ==============================================================================
def dump_env():
    """Logs the key environment variables being used."""
    keys = [
        "SDR_REMOTE", "REMOTE_PROT", "REMOTE_TIMEOUT_MS", "SPAN_RATE", "CENTER",
        "FFT_SIZE", "AUD_RATE", "GAIN_DB", "CHUNK_MS", "REMOTE_MTU",
        "METRICS_API_URL", # <-- Log the API URL
        # DO NOT log INTERNAL_API_KEY
    ]
    env_vars = {k: os.getenv(k) for k in keys}
    # Add a key to show if metrics are enabled or not, without logging the key
    env_vars["METRICS_ENABLED"] = "True" if (API_KEY and API_URL) else "False"
    jlog("env", **env_vars)

async def main():
    """Main application entrypoint."""
    dump_env() # Log configuration

    if os.getenv("DIAG", "0").lower() in ("1", "true", "yes"):
        try: _ = pick_working_rate(SPAN_RATE); log("[diag] SUCCESS: a rate works"); sys.exit(0)
        except Exception as e: log("[diag] FAILURE:", e); sys.exit(2)

    core = CoreSDR()
    log(f"Remote RTL @ {REMOTE_URL}  tuned {CENTER/1e6:.3f} MHz  ZMQ {ZMQ_ADDR}")

    audio_srv = websockets.serve(lambda w, p: audio_handler(w, p, core), "", 3050, max_size=None)
    wf_srv = websockets.serve(lambda w, p: wf_handler(w, p, core), "", 3051, max_size=None)

    async with audio_srv, wf_srv:
        log("🔊  ws://<host>:3050  (tune JSON ↔ 48 kHz audio)")
        log("🌈  ws://<host>:3051  (waterfall)")
        await asyncio.Future() # Run forever

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: exit(0))
    asyncio.run(main())
