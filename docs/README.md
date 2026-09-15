# Documentation

A [specification](../specs) pins one behaviour, a page here explains what no single file shows, and
a docstring states only what one thing does. Each fact appears in exactly one of the three, and the
other two link to it.

| Page | Covers |
|---|---|
| [reference/calibration.md](reference/calibration.md) | **Start here to tune a camera, a hand or a face**: the ordered procedure |
| [reference/configuration.md](reference/configuration.md) | Every `config.json` key, its default and its meaning |
| [reference/response-dsl.md](reference/response-dsl.md) | The language a command maps to |
| [reference/gesture-tuning.md](reference/gesture-tuning.md) | Measuring a hand threshold, and reading the report |
| [reference/face-gesture-tuning.md](reference/face-gesture-tuning.md) | Measuring a facial threshold |
| [reference/absolute-pointing.md](reference/absolute-pointing.md) | The two pointer laws, and screen calibration |
| [architecture/overview.md](architecture/overview.md) | The layering rule, and the placement of a change |
| [architecture/concurrency.md](architecture/concurrency.md) | Threads, locks, and the lock-order defect |
| [architecture/failure-model.md](architecture/failure-model.md) | The scope of each failure, and its recovery |
| [architecture/configuration-pipeline.md](architecture/configuration-pipeline.md) | Load, validate, and the scope of a reload |
| [architecture/call-paths.md](architecture/call-paths.md) | Dispatch order, and symptom to frame |
| [development/testing.md](development/testing.md) | The seams that make hardware-free tests work |
| [development/known-gaps.md](development/known-gaps.md) | Where `specs/` and the code disagree |

Design decisions are numbered in [`specs/README.md`](../specs/README.md#decisions) and are cited as
"decision *n*". `tests/documentation/` checks the reference against `settings.py`.
