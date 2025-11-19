# Core Cast Server

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

This repository contains the source code for the **Core Cast Server**, the central processing backend of the Core Cast platform.

This server is responsible for:
* Securely connecting to remote SDR hosts.
* Running the **GNU Radio** processing flowgraphs.
* Performing all server-side Digital Signal Processing (DSP).
* Demodulating audio and generating waterfall data.
* Serving audio and spectral data via **WebSockets** to multiple web clients simultaneously.

---

## Documentation

This repository contains the application code, but all documentation, setup guides, and architectural details are centralized on the main Core Cast docs website.

**For all information on how to configure, deploy, and manage the Core Cast Server, please see the official documentation:**

➡️ **[https://docs.corecastsdr.com/docs/server-setup/setup](https://docs.corecastsdr.com/docs/server-setup/setup)** ⬅️

Keeping the documentation in one place ensures it stays up-to-date and accurate.

---

## Contributing to the Docs

Find a typo, want to improve a guide, or have a new idea for the documentation? We welcome all contributions!

The documentation is managed in its own repository. Please open issues or pull requests there:

* **Documentation Repository:** [https://github.com/corecastsdr/corecast-docs](https://github.com/corecastsdr/corecast-docs/blob/main/docs/server-setup/setup.mdx)

---

## License

This project is licensed under the **GNU General Public License v3.0 (GPLv3)**.

This license is required because the software is a derivative work of the GNU Radio project (which is also licensed under GPLv3). You can view the full license text in the `LICENSE` file.

