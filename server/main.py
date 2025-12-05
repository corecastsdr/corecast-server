#!/usr/bin/env python3
#
# Core Cast Server - Main Application (IPC Socket Fix)
#
import importlib, sys, time, os, random, asyncio, json, signal, itertools
import numpy as np, websockets
import httpx
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

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

# --- Import Docker SDK ---
try:
    import docker
except ImportError:
    log("⚠️  docker library not found. Run 'pip install docker'. Polling will be disabled.")
    docker = None

# ───────── env helpers ─────────
def env_int(name, default): return int(os.getenv(name, default))
def env_float(name, default): return float(os.getenv(name, default))

# ───────── RF params ─────────
CENTER     = env_float("CENTER",     104.10e6)
SPAN_RATE  = env_int  ("SPAN_RATE",  2_048_000)
FFT_SIZE   = env_int  ("FFT_SIZE",   1024)
AUD_RATE   = env_int  ("AUD_RATE",   48_000)
GAIN_DB    = env_float("GAIN_DB",    20.0)
CHUNK_MS   = env_int  ("CHUNK_MS",   50)

# --- Role and Scaling Configuration ---
ROLE = os.getenv("ROLE", "all").lower()
MAX_CLIENTS = env_int("MAX_CLIENTS", 0)

# --- ▼▼▼ SDR CONNECTION LOGIC ▼▼▼ ---
SDR_CONNECTION_TYPE = os.getenv("SDR_CONNECTION_TYPE", "tunnel").lower()
SDR_REMOTE_ENV = os.getenv("SDR_REMOTE")
REMOTE_URL = ""

if SDR_CONNECTION_TYPE == "direct":
    if not SDR_REMOTE_ENV:
        log("❌ FATAL: SDR_CONNECTION_TYPE=direct but SDR_REMOTE environment variable is not set!")
        sys.exit(1)
    REMOTE_URL = SDR_REMOTE_ENV
    log(f"✅ SDR Connection: DIRECT mode. Connecting to Soapy server at {REMOTE_URL}")
else:
    REMOTE_URL = "127.0.0.1:55132" # Default tunnel address
    log(f"✅ SDR Connection: TUNNEL mode. Connecting to SSH tunnel at {REMOTE_URL}")

# --- Soapy Remote Config ---
REMOTE_PROT  = os.getenv("REMOTE_PROT", "tcp")
REMOTE_TO_MS = env_int("REMOTE_TIMEOUT_MS", 8000)
REMOTE_MTU   = os.getenv("REMOTE_MTU")

# --- ZMQ Configuration (THE FIX) ---
ZMQ_PORT = env_int("ZMQ_PORT", 5678)
ZMQ_HOST_ADDR = os.getenv("ZMQ_HOST_ADDR")

if ROLE == "all":
    # CRITICAL FIX: Use IPC (Unix Sockets) instead of TCP.
    # Proxychains ignores IPC, so this audio data will stay local and NOT go down the tunnel.
    ZMQ_BIND_ADDR = "ipc:///tmp/corecast_audio.ipc"
    ZMQ_CONNECT_ADDR = ZMQ_BIND_ADDR
    log(f"✅ ROLE=all: Using IPC socket for audio bypass: {ZMQ_BIND_ADDR}")

elif ROLE == "worker":
    if not ZMQ_HOST_ADDR:
        log("❌ FATAL: ROLE=worker but ZMQ_HOST_ADDR is not set!")
        sys.exit(1)
    ZMQ_BIND_ADDR = f"tcp://0.0.0.0:{ZMQ_PORT}" # Not used by worker
    ZMQ_CONNECT_ADDR = ZMQ_HOST_ADDR

else: # Controller
    ZMQ_BIND_ADDR = f"tcp://0.0.0.0:{ZMQ_PORT}"
    ZMQ_CONNECT_ADDR = f"tcp://127.0.0.1:{ZMQ_PORT}"

# --- WebSocket Config ---
WEBSOCKET_PORT = env_int("WEBSOCKET_PORT", 50350)

# --- Metrics API Configuration ---
API_KEY = os.getenv("INTERNAL_API_KEY")
API_URL = os.getenv("METRICS_API_URL")
http_client = None

if API_KEY and API_URL:
    log("✅ Metrics API enabled.")
    # Note: If proxychains is active, this external HTTP request might fail if not excluded.
    # However, since IPC fixed the critical audio loop, we can tolerate API failures if they occur.
    http_client = httpx.Client(
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        timeout=5.0
    )
else:
    log("⚠️  Metrics API key/URL not set. Skipping stats logging.")

# --- Soapy Device String ---
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

# --- DSP/App Globals ---
LOW_HZ   = CENTER - SPAN_RATE/2
HIGH_HZ  = CENTER + SPAN_RATE/2
BIN_HZ   = SPAN_RATE / FFT_SIZE
CHUNK_S  = AUD_RATE * CHUNK_MS // 1000
active_sessions = set()

# --- Globals for Controller Polling ---
TOTAL_LISTENER_COUNT = 0
POLLING_HTTP_CLIENT = httpx.Client(timeout=2.0)
try:
    DOCKER_CLIENT = docker.from_env() if docker else None
except Exception as e:
    DOCKER_CLIENT = None

# ───────── Rate Negotiation ─────────
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

# ───────── CoreSDR Class ─────────
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
        log(f"ZMQ PUB Sink binding to {ZMQ_BIND_ADDR}")
        pub = zeromq.pub_sink(gr.sizeof_gr_complex, 1, ZMQ_BIND_ADDR, 100, False, -1)
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
        frame = np.array(data[:FFT_SIZE], np.float32)
        self._sink.reset()
        return frame

# ───────── ClientRx Class ─────────
class ClientRx(gr.top_block):
    _ids = itertools.count(1)
    def __init__(self, off_hz, mode, bw, sql):
        super().__init__(f"Rx#{next(self._ids)}")
        self.off, self.mode, self.bw, self.sql = off_hz, (mode or "wbfm").lower(), bw, sql
        self._build(); self.start()
    def _build(self):
        self.lock(); self.disconnect_all()
        log(f"ClientRx connecting to ZMQ SUB: {ZMQ_CONNECT_ADDR}")
        zmq = zeromq.sub_source(gr.sizeof_gr_complex, 1, ZMQ_CONNECT_ADDR, 100, False, True)
        bw = max(10e3, min(180e3, self.bw or 200e3))
        taps = firdes.low_pass(1, SPAN_RATE, bw/2, bw/4)
        dec = SPAN_RATE // 8
        mix = filter.freq_xlating_fir_filter_ccf(8, taps, -self.off, SPAN_RATE)
        m = self.mode #
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

# ───────── AudioSession Class ─────────
class AudioSession:
    def __init__(self, ws, first, user_uuid, station_uuid):
        log(f"New audio session for user {user_uuid} on station {station_uuid}")
        self.ws = ws
        self.user_uuid = user_uuid
        self.station_uuid = station_uuid
        self.start_time = time.time()
        self.session_db_id = None
        self.client_ip = ws.remote_address[0] if ws.remote_address else "unknown"
        self.rx = ClientRx(first["freq"]-CENTER, first.get("mode","wbfm"), first.get("bw"), first.get("sql"))

    def log_session_start(self):
        if not http_client or not self.user_uuid or not self.station_uuid:
            if not http_client: log("Metrics API client not configured. Skipping stats.")
            else: log(f"Session start aborted: missing user_uuid ({self.user_uuid}) or station_uuid ({self.station_uuid})")
            return
        payload = {
            "user_uuid": self.user_uuid, "station_uuid": self.station_uuid,
            "client_ip": self.client_ip, "container_id": os.getenv("HOSTNAME", "unknown_sdr_server"),
            "freq_hz": self.rx.off + CENTER, "mode": self.rx.mode, "bw": self.rx.bw, "sql_level": self.rx.sql,
        }
        try:
            res = http_client.post(f"{API_URL}/session/start", json=payload)
            if res.status_code == 200:
                self.session_db_id = res.json().get("session_id")
                log(f"Logged session start for user {self.user_uuid}, db_id: {self.session_db_id}")
            else: log(f"API Error (start): {res.status_code} - {res.text}")
        except Exception as e: log(f"API Exception (start): {e}")

    def log_session_end(self):
        if not http_client or not self.session_db_id:
            if not http_client: log("Metrics API client not configured. Skipping stats.")
            else: log(f"Session end aborted for user {self.user_uuid}: no session_db_id (start log likely failed)")
            return
        duration = time.time() - self.start_time
        payload = { "session_db_id": self.session_db_id, "duration_sec": duration }
        try:
            res = http_client.post(f"{API_URL}/session/end", json=payload)
            log(f"Logged session end for user {self.user_uuid}, db_id: {self.session_db_id}, duration: {duration:.2f}s. Status: {res.status_code}")
        except Exception as e: log(f"API Exception (end): {e}")

    async def pump_audio(self):
        while True:
            data = self.rx.pull()
            if data:
                await self.ws.send(data)
            else:
                await asyncio.sleep(CHUNK_MS/2000)

    async def ctl(self):
        async for txt in self.ws:
            if not isinstance(txt,str): continue
            try: cmd=json.loads(txt)
            except: continue
            if cmd.get("type")!="tune": continue
            f=cmd.get("freq")
            if f is not None and abs(f-CENTER)>SPAN_RATE/2: continue
            self.rx.retune(off_hz=f-CENTER if f is not None else None,mode=cmd.get("mode"),bw=cmd.get("bw"),sql=cmd.get("sql"))
            jlog("tune", **cmd)

    async def run(self):
        try:
            active_sessions.add(self)
            self.log_session_start()
            await asyncio.gather(self.pump_audio(), self.ctl())
        finally:
            active_sessions.remove(self)
            self.log_session_end()
            self.rx.close()

# ───────── WfSession Class ─────────
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
            if line is not None:
                a,b = self._slice()
                payload = line[a:b].astype(np.float32).tobytes()
                try: await self.ws.send(payload)
                except Exception: break
            await asyncio.sleep(CHUNK_MS/1000)

    async def run(self, core):
        await asyncio.gather(self.pump(core),self.ctl())


# --- Main WebSocket Handler ---
async def main_ws_handler(ws, path, core, role):
    log(f"New WS connection from {ws.remote_address[0]} on path '{path}' (ROLE={role})")
    if path == "/audio":
        if role in ("worker", "all"): await audio_handler(ws, path, core)
        else: await ws.close(4003, "Audio service not available")
    elif path == "/waterfall":
        if role in ("controller", "all") and core: await wf_handler(ws, path, core)
        else: await ws.close(5000, "Waterfall service misconfigured")
    else:
        await ws.close(4004, f"Invalid path")

# ───────── WebSocket Handlers ─────────
async def audio_handler(ws, _p, core):
    first_packet_str = ""
    try:
        if MAX_CLIENTS > 0 and len(active_sessions) >= MAX_CLIENTS:
            await ws.close(4005, "server full"); return

        first_packet_str = await ws.recv()
        first = json.loads(first_packet_str)

        if first.get("type") != "tune": await ws.close(4000, "need tune"); return
        if abs(first["freq"] - CENTER) > SPAN_RATE/2: await ws.close(4001, "out of span"); return

        user_uuid = first.get("user_uuid")
        station_uuid = first.get("station_uuid")
        await AudioSession(ws, first, user_uuid, station_uuid).run()

    except websockets.ConnectionClosed: pass
    except Exception as e: log(f"Audio handler error: {e}")

async def wf_handler(ws, _p, core):
    try: await WfSession(ws,).run(core)
    except websockets.ConnectionClosed: pass


# ───────── Controller's Total Listener Poller ─────────
def poll_worker_metrics():
    global TOTAL_LISTENER_COUNT
    if not DOCKER_CLIENT:
        if ROLE == "all":
            while True:
                TOTAL_LISTENER_COUNT = len(active_sessions)
                time.sleep(10)
        return

    WORKER_SERVICE_NAME = os.getenv("WORKER_SERVICE_NAME", "corecast_worker")
    log(f"✅ Controller listener polling thread started. Watching service: {WORKER_SERVICE_NAME}")
    while True:
        total_clients = 0
        try:
            tasks = DOCKER_CLIENT.api.tasks_list(filters={'service': WORKER_SERVICE_NAME, 'desired-state': 'running'})
            for task in tasks:
                if task['Status']['State'] != 'running' or not task.get('NetworksAttachments'): continue
                for net in task['NetworksAttachments']:
                    if net.get('Addresses'):
                        ip = net['Addresses'][0].split('/')[0]
                        try:
                            res = POLLING_HTTP_CLIENT.get(f"http://{ip}:8001/metrics")
                            if res.status_code == 200: total_clients += res.json().get("client_count", 0)
                        except Exception: pass
                        break
            if ROLE == "all": total_clients += len(active_sessions)
            TOTAL_LISTENER_COUNT = total_clients
        except Exception as e:
            if ROLE == "all": TOTAL_LISTENER_COUNT = len(active_sessions)
        time.sleep(10)

# ───────── Public-Facing HTTP Server ─────────
class PublicMetricsHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
    def do_GET(self):
        if self.path == '/total_listeners':
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
            count = len(active_sessions) if ROLE == "all" else TOTAL_LISTENER_COUNT
            self.wfile.write(json.dumps({"total_count": count}).encode('utf-8'))
        else: self.send_response(404); self.end_headers()
    def log_message(self, format, *args): return

def start_public_metrics_server(port=8002):
    try: HTTPServer(('', port), PublicMetricsHandler).serve_forever()
    except Exception as e: log(f"❌ FATAL: Public metrics server failed: {e}")

# ───────── Local Metrics Server ─────────
class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/metrics':
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps({"client_count": len(active_sessions)}).encode('utf-8'))
        else: self.send_response(404); self.end_headers()
    def log_message(self, format, *args): return

def start_metrics_server(port=8001):
    try: HTTPServer(('', port), MetricsHandler).serve_forever()
    except Exception as e: log(f"❌ FATAL: Metrics server failed to start: {e}")

# ───────── main() ─────────
async def main():
    log(f"--- Starting Core Cast Server in ROLE='{ROLE}' ---")
    core = None
    tasks = []
    start_ws_server = False

    if ROLE in ("controller", "all"):
        if os.getenv("DIAG", "0").lower() in ("1", "true", "yes"):
            try: _ = pick_working_rate(SPAN_RATE); sys.exit(0)
            except Exception: sys.exit(2)

        log("Starting CoreSDR...")
        core = CoreSDR()
        log(f"Remote SDR @ {REMOTE_URL} ({SDR_CONNECTION_TYPE}) tuned {CENTER/1e6:.3f} MHz. ZMQ_BIND={ZMQ_BIND_ADDR}")

        threading.Thread(target=start_public_metrics_server, args=(8002,), daemon=True).start()
        if ROLE == "controller": threading.Thread(target=poll_worker_metrics, daemon=True).start()
        if ROLE == "all": await asyncio.sleep(2.0)
        start_ws_server = True

    if ROLE in ("worker", "all"):
        threading.Thread(target=start_metrics_server, args=(8001,), daemon=True).start()
        start_ws_server = True

    if start_ws_server:
        log(f"Starting consolidated WebSocket Server (port {WEBSOCKET_PORT})...")
        ws_srv = await websockets.serve(lambda ws, p: main_ws_handler(ws, p, core, ROLE), "", WEBSOCKET_PORT, max_size=None)
        tasks.append(ws_srv.serve_forever())
        log(f"🚀  ws://<host>:{WEBSOCKET_PORT}/ (serving paths based on ROLE='{ROLE}')")

    if not tasks: sys.exit(1)
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: exit(0))
    asyncio.run(main())
