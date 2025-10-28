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
# Configuration is handled via environment variables.
#

import importlib, sys, time, os, random, asyncio, json, signal, itertools
import numpy as np, websockets

# ---- GR compat shim ----
# Ensures compatibility with different GNURadio 3.8+ versions
# by manually aliasing hier_block2 and top_block if they aren't present.
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
# Load all configuration from environment variables
CENTER     = env_float("CENTER",     104.10e6)
SPAN_RATE  = env_int  ("SPAN_RATE",  2_048_000) # Target sample rate
FFT_SIZE   = env_int  ("FFT_SIZE",   2048)
AUD_RATE   = env_int  ("AUD_RATE",   48_000)   # Final audio output rate
GAIN_DB    = env_float("GAIN_DB",    20.0)
CHUNK_MS   = env_int  ("CHUNK_MS",   20)      # Audio packet duration

# --- Soapy Remote Config ---
REMOTE_URL   = os.getenv("SDR_REMOTE", "127.0.0.1:6000")
REMOTE_PROT  = os.getenv("REMOTE_PROT", "tcp")
REMOTE_TO_MS = env_int("REMOTE_TIMEOUT_MS", 8000)
REMOTE_MTU   = os.getenv("REMOTE_MTU") # <-- CRITICAL: Read the MTU variable

# ==============================================================================
# === THE CORRECT, ROBUST WAY TO BUILD THE DEVICE STRING ===
# ==============================================================================
# Dynamically build the SoapySDR device string from env vars
dev_parts = [
    f"driver=remote",
    f"remote=tcp://{REMOTE_URL}",
    f"remote:prot={REMOTE_PROT}",
    f"remote:timeout={REMOTE_TO_MS}",
]
# Only add MTU to the string if it's explicitly set
if REMOTE_MTU:
    dev_parts.append(f"remote:mtu={REMOTE_MTU}")

DEV_STR = ",".join(dev_parts)
log(f"DEBUG: Attempting to connect with device string: {DEV_STR}") # <-- CRITICAL: Our debug line
# ==============================================================================

# --- DSP/App Globals ---
ZMQ_ADDR = f"tcp://127.0.0.1:{random.randint(5600, 5900)}" # Random port for ZMQ
LOW_HZ   = CENTER - SPAN_RATE/2
HIGH_HZ  = CENTER + SPAN_RATE/2
BIN_HZ   = SPAN_RATE / FFT_SIZE # Hz per FFT bin
CHUNK_S  = AUD_RATE * CHUNK_MS // 1000 # Audio chunk size in samples

def hz_to_bin(hz: float) -> int:
    """Converts a frequency in Hz to its corresponding FFT bin index."""
    return int(max(0, min(FFT_SIZE-1, round((hz-LOW_HZ)/BIN_HZ))))

def _open_device_for_probe():
    """
    Opens a raw SoapySDR.Device instance to query its capabilities.
    Used for sample rate negotiation.
    """
    dev = s.Device(DEV_STR)
    info = dev.getHardwareInfo()
    caps = {"hardwareKey": dev.getHardwareKey(), "info": {k: str(v) for k,v in info.items()}}
    # Try to get valid sample rate ranges
    try:
        rngs = dev.getSampleRateRange(SOAPY_SDR_RX, 0); caps["rate_ranges"] = [(int(r.minimum()), int(r.maximum())) for r in rngs]
    except Exception as e: caps["rate_ranges_err"] = str(e)
    # Try to get a list of discrete valid rates
    try:
        lst = dev.listSampleRates(SOAPY_SDR_RX, 0); caps["rate_list"] = [int(x) for x in lst] if lst else []
    except Exception as e: caps["rate_list_err"] = str(e)
    return dev, caps

def _try_dev_setrate(dev, val:int) -> bool:
    """
    Attempts to set the sample rate using the raw SoapySDR.Device API.
    This is the first-pass attempt.
    """
    try:
        dev.setFrequency(SOAPY_SDR_RX, 0, CENTER); dev.setGain(SOAPY_SDR_RX, 0, "TUNER", GAIN_DB); time.sleep(0.05)
        dev.setSampleRate(SOAPY_SDR_RX, 0, val)
        got = int(dev.getSampleRate(SOAPY_SDR_RX, 0))
        log(f"[probe] (Device) ✓ setSampleRate({val}) -> {got}"); return True
    except Exception as e:
        log(f"[probe] (Device) ✗ setSampleRate({val}) -> {e}"); time.sleep(0.05); return False

def _try_block_setrate(val:int) -> bool:
    """
    Attempts to set the sample rate using the GNURadio soapy.source block.
    This is a fallback for devices that fail the raw API check.
    """
    try:
        src = soapy.source(DEV_STR, "fc32", 1, "", "bufflen=16384", [""], [""])
        src.set_frequency(0, CENTER); src.set_gain(0, "TUNER", GAIN_DB); time.sleep(0.05)
        src.set_sample_rate(0, val)
        log(f"[probe] (GR block) ✓ set_sample_rate({val})"); return True
    except Exception as e:
        log(f"[probe] (GR block) ✗ set_sample_rate({val}) -> {e}"); time.sleep(0.05); return False

def pick_working_rate(target:int) -> int:
    """
    Probes the remote SoapySDR device to find a stable, working sample rate.
    It tries a list of common rates, preferring the target rate.
    """
    log(f"[probe] opening {REMOTE_URL} (prot={REMOTE_PROT}, timeout={REMOTE_TO_MS}ms)")
    dev, caps = _open_device_for_probe()
    jlog("remote_caps", remote=REMOTE_URL, **caps)
    # List of common, RTL-SDR friendly sample rates
    cands = [target, 2_048_000, 2_000_000, 1_920_000, 1_792_000, 1_536_000, 1_280_000, 1_024_000, 900_001, 2_560_000, 2_880_000, 3_200_000]
    # Filter candidates by device's reported valid ranges
    if "rate_ranges" in caps: cands = [c for c in cands if any(lo <= c <= hi for lo,hi in caps["rate_ranges"])]
    # Path A: Try raw device API
    for c in cands:
        if _try_dev_setrate(dev, c): return c
    # Path B: Try GR block API
    for c in cands:
        if _try_block_setrate(c): return c
    raise RuntimeError("No acceptable sample rate found")

class CoreSDR(gr.top_block):
    """
    The main GNU Radio flowgraph.
    Connects to the SoapySDR source, negotiates the sample rate,
    and provides two outputs:
    1. A ZMQ PUB sink for fanning out the raw I/Q data.
    2. An internal vector sink (`_sink`) with FFT data for the waterfall.
    """
    def __init__(self):
        super().__init__("core")
        # 1. Find a working sample rate
        chosen = pick_working_rate(SPAN_RATE)
        jlog("rate_selected", remote=REMOTE_URL, target=SPAN_RATE, chosen=chosen)

        # 2. Create the Soapy source block
        src = soapy.source(DEV_STR, "fc32", 1, "", "bufflen=16384", [""], [""])

        # 3. Try to set the rate, with a fallback ladder
        for attempt in (chosen, 2_048_000, 1_920_000, 1_536_000, 1_024_000):
            try: src.set_sample_rate(0, attempt); log(f"[gr] src.set_sample_rate -> {attempt}"); break
            except Exception as e: log(f"[gr] set_sample_rate({attempt}) failed: {e}")
        else: raise RuntimeError("GNURadio soapy.source refused every tested rate")

        src.set_frequency(0, CENTER); src.set_gain(0, "TUNER", GAIN_DB)

        # 4. Create the ZMQ "fan-out" publisher
        # All ClientRx blocks will subscribe to this.
        pub = zeromq.pub_sink(gr.sizeof_gr_complex, 1, ZMQ_ADDR, 100, False, -1)

        # 5. Create the local waterfall/FFT "tap"
        vec = blocks.stream_to_vector(gr.sizeof_gr_complex, FFT_SIZE)
        fftb = fft.fft_vcc(FFT_SIZE, True, window.blackmanharris(FFT_SIZE), True, 3)
        mag2 = blocks.complex_to_mag_squared(FFT_SIZE)
        logp = blocks.nlog10_ff(10, FFT_SIZE, 1e-10) # Log-power
        self._sink = blocks.vector_sink_f(vlen=FFT_SIZE) # Internal sink

        # 6. Connect the flowgraph
        self.connect(src, pub) # Path 1: Source -> ZMQ Pub
        self.connect(src, vec, fftb, mag2, logp, self._sink) # Path 2: Source -> FFT -> Sink
        self.start()

    def grab_fft(self):
        """Pulls the latest FFT frame from the internal vector sink."""
        data = self._sink.data()
        if not data: return None
        frame = list(data[:FFT_SIZE]); self._sink.reset(); return frame

class ClientRx(gr.top_block):
    """
    A dedicated, dynamically-created flowgraph for a single client.
    Subscribes to the CoreSDR's ZMQ stream and performs all per-client
    DSP (tuning, filtering, demodulation).
    """
    _ids = itertools.count(1) # Class-level counter for unique IDs

    def __init__(self, off_hz, mode, bw, sql):
        super().__init__(f"Rx#{next(self._ids)}")
        self.off, self.mode, self.bw, self.sql = off_hz, (mode or "wbfm").lower(), bw, sql
        self._build(); self.start()

    def _build(self):
        """Constructs the client's DSP flowgraph."""
        # Lock the flowgraph to make changes
        self.lock(); self.disconnect_all()

        # 1. ZMQ Subscriber source
        zmq = zeromq.sub_source(gr.sizeof_gr_complex, 1, ZMQ_ADDR, 100, False, True)
        bw = max(10e3, min(180e3, self.bw or 200e3)) # Clamp bandwidth

        # 2. Frequency Translator (The "Tuner")
        # This block "tunes" by mixing the stream
        taps = firdes.low_pass(1, SPAN_RATE, bw/2, bw/4)
        dec = SPAN_RATE // 8 # Decimation rate
        mix = filter.freq_xlating_fir_filter_ccf(8, taps, -self.off, SPAN_RATE)

        # 3. Demodulator (based on mode)
        m = self.mode
        if m=="wbfm": demod = analog.wfm_rcv(quad_rate=dec, audio_decimation=int(dec//AUD_RATE))
        elif m=="nbfm": demod = analog.nbfm_rx(audio_rate=AUD_RATE, quad_rate=dec, tau=750e-6, max_dev=bw/4)
        elif m=="am": demod = analog.am_demod_cf(dec, int(dec//AUD_RATE), bw/4, bw/2)
        elif m in ("usb","lsb"): demod = analog.ssbdemod_cf(dec, AUD_RATE, bw/4, 1 if m=="usb" else 0)
        else: raise ValueError("bad mode")

        # 4. Squelch (optional)
        if self.sql is not None:
            squelch = analog.pwr_squelch_ff(self.sql, 1e-3, 0, True)
            self.connect(zmq, mix, demod, squelch); tail = squelch
        else:
            self.connect(zmq, mix, demod); tail = demod

        # 5. Final audio sink
        self.snk = blocks.vector_sink_f()
        self.connect(tail, self.snk); self.unlock()

    def retune(self,*,off_hz=None,mode=None,bw=None,sql=None):
        """
        Updates the client's DSP parameters and rebuilds the flowgraph.
        This is called by the WebSocket control loop.
        """
        if off_hz is not None: self.off=off_hz
        if mode is not None: self.mode=mode.lower()
        if bw is not None: self.bw=bw
        if sql is not None: self.sql=sql
        self._build() # Re-run the build process

    def pull(self):
        """Grabs the processed audio samples from the internal sink."""
        pcm = np.array(self.snk.data(), np.float32); self.snk.reset(); return pcm.tobytes()

    def close(self):
        """Stops and waits for the flowgraph to shut down."""
        self.stop(); self.wait()

class AudioSession:
    """
    Manages the WebSocket lifecycle for a single *audio* client.
    Contains the async loops for pumping audio and receiving tune commands.
    """
    def __init__(self, ws, first):
        """Links the WebSocket to a new ClientRx flowgraph."""
        self.ws = ws
        # Create a dedicated DSP block for this client
        self.rx = ClientRx(first["freq"]-CENTER, first.get("mode","wbfm"), first.get("bw"), first.get("sql"))

    async def pump_audio(self):
        """Async loop: Continuously pulls audio from ClientRx and sends it over the WebSocket."""
        while True:
            data = self.rx.pull()
            if data:
                await self.ws.send(data)
            else:
                # Sleep if no data to avoid busy-waiting
                await asyncio.sleep(CHUNK_MS/2000) # Sleep for 1/2 chunk time

    async def ctl(self):
        """Async loop: Listens for JSON 'tune' commands from the client and retunes the ClientRx."""
        async for txt in self.ws:
            if not isinstance(txt,str): continue
            try: cmd=json.loads(txt)
            except: continue
            if cmd.get("type")!="tune": continue
            # Basic validation
            f=cmd.get("freq")
            if f is not None and abs(f-CENTER)>SPAN_RATE/2: continue # Out of bounds
            # Apply new settings
            self.rx.retune(off_hz=f-CENTER if f is not None else None,mode=cmd.get("mode"),bw=cmd.get("bw"),sql=cmd.get("sql"))
            jlog("tune",**cmd)

    async def run(self):
        """Runs the audio pump and control loops concurrently."""
        try: await asyncio.gather(self.pump_audio(),self.ctl())
        finally: self.rx.close() # Ensure flowgraph is stopped on exit

class WfSession:
    """
    Manages the WebSocket lifecycle for a single *waterfall* client.
    Contains async loops for pumping FFT data and receiving span commands.
    """
    def __init__(self, ws):
        self.ws = ws
        self.minH, self.maxH = LOW_HZ, HIGH_HZ # Default to full span

    def _slice(self):
        """Calculates the start/end FFT bin indices from the current zoomed view."""
        a,b=hz_to_bin(self.minH),hz_to_bin(self.maxH); return min(a,b),max(a,b)

    async def ctl(self):
        """Async loop: Listens for JSON 'span' commands to update the zoom/pan."""
        async for txt in self.ws:
            try: cmd=json.loads(txt)
            except: continue
            if cmd.get("type")!="span": continue
            lo,hi=max(LOW_HZ,min(HIGH_HZ,float(cmd.get("min",LOW_HZ)))),max(LOW_HZ,min(HIGH_HZ,float(cmd.get("max",HIGH_HZ))))
            if hi-lo>=BIN_HZ: self.minH,self.maxH=lo,hi

    async def pump(self, core):
        """Async loop: Continuously pulls FFT data from CoreSDR, slices it, and sends it."""
        while True:
            line = core.grab_fft()
            if line:
                a,b=self._slice() # Get current view
                await self.ws.send(json.dumps({"type":"waterfall","data":line[a:b]}))
            await asyncio.sleep(CHUNK_MS/1000) # ~50 FPS

    async def run(self, core):
        """Runs the data pump and control loops concurrently."""
        await asyncio.gather(self.pump(core),self.ctl())

async def audio_handler(ws, _p, core):
    """
    Main entrypoint for new audio WebSocket connections (port 3050).
    Validates the first packet and starts an AudioSession.
    """
    try:
        # Wait for the first "tune" packet
        first = json.loads(await ws.recv())
        if first.get("type")!="tune": await ws.close(4000,"need tune"); return
        if abs(first["freq"]-CENTER)>SPAN_RATE/2: await ws.close(4001,"out of span"); return
        # Hand off to a new session
        await AudioSession(ws, first).run()
    except websockets.ConnectionClosed: pass # Handle disconnects gracefully

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
    keys = ["SDR_REMOTE", "REMOTE_PROT", "REMOTE_TIMEOUT_MS", "SPAN_RATE", "CENTER",
            "FFT_SIZE", "AUD_RATE", "GAIN_DB", "CHUNK_MS", "REMOTE_MTU"]
    jlog("env", **{k: os.getenv(k) for k in keys})

async def main():
    """Main application entrypoint."""
    dump_env() # Log configuration

    # --- DIAG mode ---
    # If DIAG=1, just run the rate check and exit.
    if os.getenv("DIAG", "0").lower() in ("1", "true", "yes"):
        try: _ = pick_working_rate(SPAN_RATE); log("[diag] SUCCESS: a rate works"); sys.exit(0)
        except Exception as e: log("[diag] FAILURE:", e); sys.exit(2)

    # 1. Start the one-and-only CoreSDR flowgraph
    core = CoreSDR()
    log(f"Remote RTL @ {REMOTE_URL}  tuned {CENTER/1e6:.3f} MHz  ZMQ {ZMQ_ADDR}")

    # 2. Start the WebSocket servers
    audio_srv = websockets.serve(lambda w, p: audio_handler(w, p, core), "", 3050, max_size=None)
    wf_srv = websockets.serve(lambda w, p: wf_handler(w, p, core), "", 3051, max_size=None)

    async with audio_srv, wf_srv:
        log("🔊  ws://<host>:3050  (tune JSON ↔ 48 kHz audio)")
        log("🌈  ws://<host>:3051  (waterfall)")
        await asyncio.Future() # Run forever

if __name__ == "__main__":
    # Set up a signal handler for graceful shutdown (Ctrl+C)
    signal.signal(signal.SIGINT, lambda *_: exit(0))
    asyncio.run(main())
