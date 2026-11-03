# TiFigure Lab

**0.8.1 preview — local paper extraction and human review.** Upload a PDF, run a bounded extraction task, then inspect source evidence and correct pending results. React + TypeScript frontend, FastAPI + Pillow backend, SQLite storage. The interface is in English.

| Workflow | Current scope |
| --- | --- |
| Reported values | Extract numerical statements from native PDF text with source evidence |
| Marker graphs | Model locates panels/ticks/markers; local code calculates coordinates |
| XRD (experimental) | Model proposals plus independent pixel candidates; pending peak positions only |
| Micrographs (experimental) | Model proposes scale/objects; local region tools attempt contours and areas |
| Human review | Edit calibration, points and polygons; retain evidence and review history |
| Archiving | Preserve PDF, request rules, inputs/outputs, crops and review revisions |

**Limitations:** peak and region candidates require review. Tiny peaks, touching annotations, broken traces and ambiguous scales can cause failures. This is an experimental research prototype, not validated automatic dataset generation. No model is trained by using the app.

Every automatic model call includes the corresponding Markdown rules from `configs/`. Results stay pending; no automatic scientific acceptance or final master generation. Continuous function-curve tracing, OCR recovery and publisher-image retrieval are not implemented. See [workflow guide](docs/AUTOMATION.zh-CN.md).

## What works

- Calibrate linear/log graph axes; click points, or inspect experimental peak suggestions.
- Calibrate a micrograph scale bar; draw/edit lengths and polygon areas; calculate 2D equivalent-circle diameter (ECD).
- Save/reopen projects, retain source pixels and review context, export CSV and workspace JSON.
- Record author-reported scalars/ranges with source text, sample identities and uncertainty notation.
- Link eligible results to existing TiFM fields and explicitly adopt reviewed scalars into a copy, with audit/pending attachments. Original reference bytes stay unchanged.
- Optionally request extraction plans through a DeepSeek connection or the offline provider-neutral JSON bridge. Planning proposals require review. Separate paper-extraction branches execute only bounded built-in measurement tools.
- Configured native-PDF table parsers and batch-review interface; arbitrary PDF layout/OCR is not supported. The public package starts with empty table registries.

## Install on Windows

Prerequisites: Python 3.13, Node 24, and pnpm 11 on PATH. Run from this extracted source directory in PowerShell:

```powershell
.\scripts\setup.ps1 -Python python -RuntimeRoot 'D:\TiFigureLabDemo'
.\scripts\start.ps1 -RuntimeRoot 'D:\TiFigureLabDemo' -Port 8766
```

Setup installs the pinned core and PDF dependencies from their package registries; network access is needed. It puts the Python environment, frontend dependencies, package caches and installation temporary files under the requested D-drive root. The source directory contains a junction to those frontend dependencies and the compiled frontend. Preinstalled Python/Node/pnpm and operating-system/browser caches remain where they were installed.

Use API settings to configure a session key. PDF import itself makes no model call; sending an extraction request may use your provider balance.

Use a fresh source folder and a fresh runtime root for an independent installation. Setup refuses to reuse frontend dependencies from a different runtime. Start refuses a port serving another source/data directory, including older servers without installation identity. It binds to 127.0.0.1 only. This is a local browser application, not a hosted service or packaged desktop executable.

The double-click launcher uses the default `D:\TiFigureLab` and port 8765; use the commands above for a separate installation. Stop with:

```powershell
.\scripts\stop.ps1 -RuntimeRoot 'D:\TiFigureLabDemo'
```

## First demonstration

See [DEMO.md](DEMO.md). Synthetic practice images are built in. No paper downloads, API key or paid model calls are needed. `examples/synthetic-reference.json` is a teaching fixture only, never experimental or training data.

Run offline software checks from this directory:

```powershell
& 'D:\TiFigureLabDemo\runtime\venv\Scripts\python.exe' -m scripts.smoke_release
& 'D:\TiFigureLabDemo\runtime\venv\Scripts\python.exe' -m pytest tests -q -p no:cacheprovider --basetemp 'D:\TiFigureLabDemo\tmp\pytest-first-run'
```

Use a fresh pytest basetemp each run. The smoke check creates its own isolated data under `tmp/` and never contacts a model. It tests both built-in image branches, persistence, CSV and unchanged TiFM-copy export. Passing checks establish software behavior, not scientific accuracy.

## Data and API keys

Projects/images/reference files live in `<RuntimeRoot>/data`; exports in `data/exports`. Back up the entire data directory with the server stopped. Workspace JSON is an export format, not a standalone restore/import mechanism; image/PDF bytes are not embedded in master-copy ZIPs. Keep original source files.

API settings hold a DeepSeek key only in server memory until forgotten or the server stops. Only an explicit Send action uses the paid API. Selected source text/image may then leave the machine. The app is intended for a trusted single-user localhost environment.

## Scientific limits

This is a measurement/review assistant. It does not identify alpha/beta phases reliably, perform learned segmentation, retrieve higher-resolution publisher images, or reconstruct missing image detail. Gray-region assistance can fail on real micrographs. A polygon ECD is not automatically the paper's grain-size statistic; selected-object area is not a phase volume fraction. Review sample state, phase identity, definition, units, image scale and sampling scope.

Registered conflicts block adoption. Unselected original values, including erroneous or conflicting ones, remain in exported copies without new validation. Current copy export only replaces existing reviewed finite scalar fields; no new records or metadata/uncertainty rewrites. Full TiFM-schema validation and compatibility with an actual downstream training reader have not been established.

Private papers, figure crops, reference masters, model transcripts, workspace data and development validation records are not distributed in this package.

## Source-package status

This is a prepared local source package, not proof of GitHub publication. No project open-source license has been selected yet; choose one before advertising redistribution rights. Dependency licenses remain with their packages/bundled notices. The package contains no repository remote or account credentials.
