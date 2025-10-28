import os, json, signal, asyncio, websockets
import dsp
from server import SigGridServer

os.environ.setdefault("GR_SCHEDULER", "TPB")
os.environ.setdefault("TPB_THREAD_COUNT", str(os.cpu_count()))

try:
    from gnuradio import gr
    gr.enable_realtime_scheduling()
except Exception:
    pass

TICK = dsp.CHUNK_MS / 1000.0            # 0.020 s
radio = SigGridServer()
radio.start()                           # starts CoreSDR

# ───────── audio WebSocket (48 kHz float, port 3050) ────────────────────
async def audio_handler(ws, _):
    cid = None
    try:
        hello = json.loads(await ws.recv())         # must be {"type":"tune",…}
        if hello.get("type") != "tune":
            await ws.close(4000, "need tune"); return
        if abs(hello["freq"] - dsp.CENTER) > dsp.SPAN_RATE/2:
            await ws.close(4001, "out of span"); return

        cid, rx = radio.add_client(
            hello["freq"] - dsp.CENTER,
            hello.get("mode","wbfm"),
            hello.get("bw"),
            hello.get("sql"),
            hello.get("nr"),
            hello.get("notch")
        )

        async def pump():
            nxt = asyncio.get_event_loop().time()
            while True:
                for blk in rx.pull_frames():
                    await ws.send(blk)
                nxt += TICK
                await asyncio.sleep(max(0, nxt - asyncio.get_event_loop().time()))

        async def control():
            async for msg in ws:
                if not isinstance(msg, str):
                    continue
                try:
                    cmd = json.loads(msg)
                except Exception:
                    continue
                if cmd.get("type") != "tune":
                    continue
                f = cmd.get("freq")
                if f is not None and abs(f-dsp.CENTER) > dsp.SPAN_RATE/2:
                    continue
                rx.retune(
                    off_hz = f - dsp.CENTER if f is not None else None,
                    mode   = cmd.get("mode"),
                    bw     = cmd.get("bw"),
                    sql    = cmd.get("sql"),
                    nr     = cmd.get("nr"),
                    notch  = cmd.get("notch"),
                )

        await asyncio.gather(pump(), control())

    except websockets.ConnectionClosed:
        pass
    finally:
        if cid:
            radio.remove_client(cid)

# ───────── waterfall WebSocket (port 3051) ──────────────────────────────
async def wf_handler(ws, _):
    lo, hi = dsp.LOW_HZ, dsp.HIGH_HZ

    async def control():
        nonlocal lo, hi
        async for msg in ws:
            try:
                cmd = json.loads(msg)
            except Exception:
                continue
            if cmd.get("type") != "span":
                continue
            a = max(dsp.LOW_HZ,  min(float(cmd.get("min", dsp.LOW_HZ)),  dsp.HIGH_HZ))
            b = max(dsp.LOW_HZ,  min(float(cmd.get("max", dsp.HIGH_HZ)), dsp.HIGH_HZ))
            if b-a >= dsp.BIN_HZ:
                lo, hi = a, b

    def slicer():
        a, b = dsp.hz_to_bin(lo), dsp.hz_to_bin(hi)
        return min(a,b), max(a,b)

    async def pump():
        while True:
            line = radio.grab_fft()
            if line:
                a,b = slicer()
                await ws.send(json.dumps({"type":"waterfall","data":line[a:b]}))
            await asyncio.sleep(dsp.CHUNK_MS/1000)

    try:
        await asyncio.gather(pump(), control())
    except websockets.ConnectionClosed:
        pass

# ───────── main loop ────────────────────────────────────────────────────
async def main():
    print(f"🎙  RTL at {dsp.REMOTE_URL}  centre={dsp.CENTER/1e6:.3f} MHz "
          f"span=±{dsp.SPAN_RATE/2/1e6:.1f} MHz")
    audio_srv = websockets.serve(audio_handler, "", 3050, max_size=None)
    wf_srv    = websockets.serve(wf_handler,    "", 3051, max_size=None)
    async with audio_srv, wf_srv:
        await asyncio.Future()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: exit(0))
    asyncio.run(main())
