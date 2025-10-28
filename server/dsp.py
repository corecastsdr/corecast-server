# dsp.py — Core Cast DSP Module
#
# Contains robust rate negotiation logic for SoapyRemote and the
# core GNU Radio source block (CoreSDR).
# This file can be imported by other Python applications.
# Works with GNURadio 3.8–3.13.
#
import os, math, itertools, time
import numpy as np
from gnuradio import gr, blocks, filter, analog, soapy, fft
from gnuradio.fft   import window
from gnuradio.filter import firdes
import SoapySDR as s
from SoapySDR import SOAPY_SDR_RX

# ───────── logging helpers ─────────
def ts(): return time.strftime("%Y-%m-%d %H:%M:%S")
def log(*a): print(f"[{ts()}]", *a, flush=True)

# ───────── env helpers ─────────
def _i(n, d):
    """Helper to get env var as int with default."""
    try: return int(os.getenv(n, d))
    except: return d
def _f(n, d):
    """Helper to get env var as float with default."""
    try: return float(os.getenv(n, d))
    except: return d

# ───────── RF/config params (env-driven) ─────────
CENTER       = _f("CENTER",       104.10e6)
SPAN_RATE    = _i("SPAN_RATE",    2_048_000)
FFT_SIZE     = _i("FFT_SIZE",     1024)
AUD_RATE     = _i("AUD_RATE",     48_000)
GAIN_DB      = _f("GAIN_DB",      20.0)
CHUNK_MS     = _i("CHUNK_MS",     20)

# --- Soapy Remote Config ---
SDR_REMOTE   = os.getenv("SDR_REMOTE", "127.0.0.1:55132")
REMOTE_PROT  = os.getenv("REMOTE_PROT", "tcp")         # "tcp" recommended; set "udp" if desired
REMOTE_TO_MS = _i("REMOTE_TIMEOUT_MS", 8000)           # control socket RPC timeout (ms)
REMOTE_MTU   = os.getenv("REMOTE_MTU")                 # optional: e.g. "1200"
REMOTE_WIN   = os.getenv("REMOTE_WINDOW")              # optional: e.g. "44040192"

# --- Build the Soapy "remote" device string with extras ---
_dev_parts = [
    f"driver=remote",
    f"remote=tcp://{SDR_REMOTE}",
    f"remote:prot={REMOTE_PROT}",
    f"remote:timeout={REMOTE_TO_MS}",
]
if REMOTE_MTU: _dev_parts.append(f"remote:mtu={REMOTE_MTU}")
if REMOTE_WIN: _dev_parts.append(f"remote:window={REMOTE_WIN}")
DEV_STR = ",".join(_dev_parts)

# --- DSP Globals ---
LOW_HZ   = CENTER - SPAN_RATE/2
HIGH_HZ  = CENTER + SPAN_RATE/2
BIN_HZ   = SPAN_RATE / FFT_SIZE
CHUNK_S  = AUD_RATE * CHUNK_MS // 1000

def hz_to_bin(hz):
    """Converts a frequency in Hz to its corresponding FFT bin index."""
    return int(max(0, min(FFT_SIZE-1, round((hz-LOW_HZ)/BIN_HZ))))

# ───────── rate negotiation (two paths) ─────────

def _list_rate_ranges(dev):
    """Queries device for its valid continuous sample rate ranges."""
    try:
        rngs = dev.getSampleRateRange(SOAPY_SDR_RX, 0)
        return [(int(r.minimum()), int(r.maximum())) for r in rngs]
    except Exception as e:
        log("[probe] getSampleRateRange failed:", e)
        return []

def _list_rates(dev):
    """Queries device for its valid discrete sample rate list."""
    try:
        lst = dev.listSampleRates(SOAPY_SDR_RX, 0)
        return [int(x) for x in lst] if lst else []
    except Exception as e:
        log("[probe] listSampleRates failed:", e)
        return []

def _try_dev_setrate(dev, val: int) -> bool:
    """Attempts to set rate via the raw SoapySDR.Device API."""
    try:
        # Some remotes behave better if freq/gain are set first
        dev.setFrequency(SOAPY_SDR_RX, 0, CENTER)
        dev.setGain     (SOAPY_SDR_RX, 0, "TUNER", GAIN_DB)
        time.sleep(0.05)
        dev.setSampleRate(SOAPY_SDR_RX, 0, val)
        got = int(dev.getSampleRate(SOAPY_SDR_RX, 0))
        log(f"[probe] (Device) ✓ setSampleRate({val}) -> {got}")
        return True
    except Exception as e:
        log(f"[probe] (Device) ✗ setSampleRate({val}) -> {e}")
        time.sleep(0.05)
        return False

def _try_block_setrate(val: int) -> bool:
    """
    Fallback: some SoapyRemote builds accept rate changes via the
    GNURadio soapy.source even when the raw Device RPC fails.
    """
    try:
        src = soapy.source(DEV_STR, "fc32", 1, "", "bufflen=16384", [""], [""])
        src.set_frequency(0, CENTER)
        src.set_gain     (0, "TUNER", GAIN_DB)
        time.sleep(0.05)
        src.set_sample_rate(0, val)
        log(f"[probe] (GR block) ✓ set_sample_rate({val})")
        return True
    except Exception as e:
        log(f"[probe] (GR block) ✗ set_sample_rate({val}) -> {e}")
        time.sleep(0.05)
        return False

def _pick(remote: str, target: int) -> int:
    """
    Probes the remote Soapy device to find a working sample rate.
    Tries Path A (raw API) then Path B (GR block) across a list
    of candidate rates.
    """
    log(f"[probe] opening {remote} (prot={REMOTE_PROT}, timeout={REMOTE_TO_MS}ms)")
    dev = s.Device(DEV_STR)

    info = dev.getHardwareInfo()
    log("[probe] device:", dict((k, str(v)) for k, v in info.items()))
    ranges = _list_rate_ranges(dev)
    dlist  = _list_rates(dev)
    if ranges: log("[probe] ranges:", ranges)
    if dlist : log("[probe] rate_list:", dlist)

    # Candidate rates: prefer target; then common remote-friendly values
    cands = [target, 2_048_000, 2_000_000, 1_920_000, 1_792_000,
             1_536_000, 1_280_000, 1_024_000, 900_001, 2_560_000,
             2_880_000, 3_200_000]

    # If ranges are known, filter to union of ranges
    if ranges:
        cands = [c for c in cands if any(lo <= c <= hi for lo, hi in ranges)]

    # Path A: raw Soapy Device RPC
    for c in cands:
        if _try_dev_setrate(dev, c):
            return c

    # Path B: GNURadio Soapy block
    for c in cands:
        if _try_block_setrate(c):
            return c

    raise RuntimeError("No acceptable sample rate for remote")

# ───────── Core wide-band receiver block ─────────

class CoreSDR(gr.hier_block2):
    """
    A GNU Radio hierarchical block that wraps the SoapySDR source,
    handles sample rate negotiation, and provides a "tap" for
    FFT/waterfall data.

    This block has one output port:
    - out[0]: The raw, wideband I/Q stream (gr.sizeof_gr_complex)
    """
    def __init__(self):
        gr.hier_block2.__init__(self, "core",
            gr.io_signature(0,0,0), # No input ports
            gr.io_signature(1,1,gr.sizeof_gr_complex)) # One output port

        # 1. Find a working sample rate
        chosen = _pick(SDR_REMOTE, SPAN_RATE)
        log(f"[probe] chosen_rate={chosen}")

        # 2. Create the Soapy source
        src = soapy.source(DEV_STR, "fc32", 1, "", "bufflen=16384", [""], [""])

        # 3. Try to set the rate, with a fallback ladder
        for attempt in (chosen, 2_048_000, 1_920_000, 1_536_000, 1_024_000):
            try:
                src.set_sample_rate(0, attempt)
                log(f"[gr] set_sample_rate -> {attempt}")
                break
            except Exception as e:
                log(f"[gr] set_sample_rate({attempt}) failed: {e}")
        else:
            raise RuntimeError("soapy.source refused all tested rates")

        # 4. Configure the source
        src.set_frequency(0, CENTER)
        src.set_gain     (0, "TUNER", GAIN_DB)

        # 5. Create the local waterfall/FFT "tap"
        vec  = blocks.stream_to_vector(gr.sizeof_gr_complex, FFT_SIZE)
        fftb = fft.fft_vcc(FFT_SIZE, True, window.blackmanharris(FFT_SIZE), True, 3)
        mag2 = blocks.complex_to_mag_squared(FFT_SIZE)
        logp = blocks.nlog10_ff(10, FFT_SIZE, 1e-10)
        self._sink = blocks.vector_sink_f(vlen=FFT_SIZE) # Internal sink

        # 6. Connect the flowgraph
        # Path 1: Source -> Output Port
        self.connect(src, self)
        # Path 2: Source -> FFT -> Internal Sink
        self.connect(src, vec, fftb, mag2, logp, self._sink)

    def grab_fft(self):
        """Pulls the latest FFT frame from the internal sink."""
        d = self._sink.data()
        if not d: return None
        frame = np.array(d[:FFT_SIZE], np.float32).tolist()
        self._sink.reset()
        return frame
