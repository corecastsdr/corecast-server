#
# Core Cast - Main GNU Radio Server Block
#
# This file defines the top-level GNU Radio flowgraph.
# It instantiates the `dsp.CoreSDR` block and provides
# methods to dynamically add/remove `ClientRx` blocks.
#
# NOTE: This version uses direct `self.connect` for fan-out,
# which is an alternative to the ZMQ fan-out model in main.py.
#

import itertools, time
from gnuradio import gr, blocks
import dsp

class CoreCastServer(gr.top_block):
    """
    A GNU Radio top_block that manages the CoreSDR source
    and all connected ClientRx blocks.

    It directly connects/disconnects client blocks to the
    CoreSDR output port.
    """
    def __init__(self):
        super().__init__("corecast")

        # Instantiate the main SDR source block from dsp.py
        self.core  = dsp.CoreSDR()

        # Connect to a null sink by default. This is crucial
        # to keep the CoreSDR flowgraph running even when
        # no clients are connected.
        self._null = blocks.null_sink(gr.sizeof_gr_complex)
        self.connect(self.core, self._null)

        # --- Client Management ---
        self._ids   = itertools.count(1) # Unique client ID generator
        self._rxt   = {}        # cid → ClientRx (tracks active clients)
        self._start = {}        # cid → start time

    # ----- lifecycle ---------------------------------------------------
    def add_client(self, off_hz, mode, bw, sql, nr, notch):
        """
        Creates a new ClientRx, connects it to the main CoreSDR,
        and starts it.
        """
        cid = next(self._ids)
        rx  = dsp.ClientRx(off_hz, mode, bw, sql, nr, notch)

        # Must lock the flowgraph before changing connections
        self.lock()
        self.connect(self.core, rx) # Connect new client
        self.unlock()

        self._rxt[cid] = rx;  self._start[cid] = time.time()
        print(f"+ client {cid:03}  (now {len(self._rxt)})")
        return cid, rx

    def remove_client(self, cid):
        """
        Finds a client by ID, disconnects it, and stops it.
        """
        rx = self._rxt.pop(cid, None)
        if rx is None:
            return # Client not found

        # Must lock the flowgraph to modify connections
        self.lock()
        self.disconnect(self.core, rx)
        self.unlock()

        self._start.pop(cid, None)
        print(f"- client {cid:03}  (now {len(self._rxt)})")

    # ----- waterfall helper -------------------------------------------
    def grab_fft(self):
        """
        Helper method to pull the waterfall data from the core block.
        """
        return self.core.grab_fft()
