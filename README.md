<p align="center">
  <img src="docs/assets/corenovus-community-banner.png" alt="CoreNovus Community — Connected AI Workflows" />
</p>

# convilyn

[![CI](https://github.com/CoreNovus/convilyn-python/actions/workflows/ci.yml/badge.svg)](https://github.com/CoreNovus/convilyn-python/actions/workflows/ci.yml)

Official Convilyn client SDK — file conversion, agentic workflows, and a community library for reusable AI workflows.

Convilyn helps people turn repeated AI-assisted work into workflows that can be run again, shared, and improved. It is part of the CoreNovus vision for practical AI workflows across cloud services, local machines, AI PCs, edge devices, and future IoT environments.

> **Public mirror** of Convilyn's monorepo (the source of truth). Contributions are
> welcome and land in the shipped package — see
> **[CONTRIBUTING.md](CONTRIBUTING.md)** (fork → PR → upstreamed, authorship preserved).

## What is Convilyn?

Convilyn is the official Python client for the Convilyn platform, and it has two
halves.

**Offline conversion runs entirely on your machine.** `convilyn.local` works
with no API key, no account, and no network:

- **Documents to Markdown** — PDF, Word, PowerPoint, Excel, CSV, XML, plain
  text, and (with LibreOffice or Calibre installed) OpenDocument, legacy Office
  and ebook formats. Headings, lists and tables survive the trip, and embedded
  images come with it.
- **Images between formats** — PNG, JPEG, WebP, AVIF, TIFF and the rest of what
  Pillow supports on your machine. Transparency is composited when the target
  cannot carry it.
- **PDF page operations** — merge, select pages, split, rotate, compress, and
  add or remove a password.

Install only what you use (`convilyn[pdf]`, `convilyn[docx]`, `convilyn[images]`,
…); plain text and CSV need nothing at all.

```bash
pip install "convilyn[docx]"
convilyn local convert report.docx --to md
```

Run `convilyn local doctor` to see what your machine can do and how to extend
it — it never guesses, and it never fails silently.

**Platform workflows run in the cloud, and need a key.** `Convilyn(...)` reaches
the hosted AI workflows — the part where a conversion becomes something you can
save, share, re-run and improve.

The long-term goal is for more of the first half to be possible: practical AI
workflows on the computing resources people already have, including AI PCs and
edge devices. `convilyn-edge` is where that work runs today.

## Who is this for?

Convilyn is for:

- Developers building reusable AI workflow components
- People working with file conversion and automation
- Builders experimenting with local-first AI workflows
- Communities that want open, practical AI tooling
- Contributors who want to help make AI workflows easier to use and share

## Install

```bash
uv add convilyn          # or: pip install convilyn
```

### Installing a pre-release

Pin the version. Do **not** reach for `--pre`:

```bash
pip install "convilyn[all]==3.2.0b1"
```

pip already allows a pre-release when the specifier names one explicitly, so the
pin is enough on its own. `--pre` is a **global** switch — it applies to the
whole dependency resolution, not just to `convilyn` — and pip's own hint
(``install with `pip install --pre` ``) does not say so. Following that hint
pulls pre-releases of the packages underneath us too: `pydantic`, which every
model in this SDK is built on, and `lxml`, which `python-docx` / `python-pptx`
parse with. A result from that environment cannot be attributed to `convilyn`
at all.

That is pip's behaviour rather than a defect here, but anyone installing a
pre-release will meet it.

## Quickstart

See **[docs/QUICKSTART.md](docs/QUICKSTART.md)** and the full docs at:

[https://docs.convilyn.corenovus.com](https://docs.convilyn.corenovus.com)

## Community

Convilyn is maintained by CoreNovus, an independent developer team building open source tools for reusable AI workflows.

We welcome contributions from people who want to help improve documentation, examples, tests, file conversion workflows, local-first AI workflows, and developer experience.

A useful workflow should not disappear after one conversation. It should become something others can learn from, adapt, and run in their own context.

Start here:

* [Contributing Guide](CONTRIBUTING.md)
* [Code of Conduct](CODE_OF_CONDUCT.md)
* [Good first issues](https://github.com/CoreNovus/convilyn-python/labels/good%20first%20issue)
* [CoreNovus on GitHub](https://github.com/CoreNovus)

## Project direction

CoreNovus is building toward a world where AI workflows can move beyond one-time chat sessions.

Convilyn focuses on the client SDK and community-facing workflow building blocks. The broader CoreNovus ecosystem aims to make workflows easier to create, save, reuse, share, and eventually run across cloud, local, and real-world environments.

## Contributing

We use the **DCO** (`git commit -s`) — no CLA.

Start with [CONTRIBUTING.md](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md), and [good first issues](https://github.com/CoreNovus/convilyn-python/labels/good%20first%20issue).

Small documentation fixes, examples, tests, and issue reports are especially welcome.

## Security

Please report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

Do not open a public GitHub issue for security vulnerabilities.

## License

[Apache-2.0](LICENSE).
