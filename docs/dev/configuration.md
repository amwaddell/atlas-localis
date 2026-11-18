# Configuration

[`configs/defaults.yaml`](../../configs/defaults.yaml) is the default configuration; [`WorkbenchConfig`](../../src/workbench/core/config.py) defines validation. Run from the repository root so relative data paths resolve correctly.

```python
from pathlib import Path
from workbench.core.config import load_config
from workbench.graphs.research_models import ConversationInput
from workbench.systems.runner import create_application

config = load_config(overrides={"model": {"temperature": 0.3}})
# Alternatively: load_config(config_path=Path("configs/custom.yaml"))
with create_application(config=config) as app:
    result = app.invoke(ConversationInput(question="When did the Roman Republic end?"))
    print(result.answer_text)
```

| Section | Shipped defaults / role |
| --- | --- |
| `model` | `llamacpp_server`, `http://127.0.0.1:8080`, Ministral, temperature 0.7, max tokens 2048, timeout 60 seconds |
| `embeddings` | In-process `local` E5, `mps`, batch size 4; normalized 768-dimensional vectors with query/passage prefixes |
| `retrieval` | Hybrid; 20 keyword and 20 vector candidates, RRF k=60, merge cap 50, final top-k 5 |
| `reranking` | Enabled in-process `cross_encoder` BGE, `mps`, batch size 1, pair window 8192 |
| `context` | Total prompt budget 6000, output allowance 1200, maximum 3 chunks per document |
| `chunking` | Paragraph-aware character units: target 1000, max setting 1500, minimum 100; long paragraphs preserved |
| `paths` | Local `data/`; active index under `data/indexes/active` |
| `runs` | Local output, logs, traces and reports under `runs/` |
| `observability` | Tracing and metrics enabled; local Phoenix viewer URL; export requires a separate explicit endpoint |

`mps`/`auto` choose Metal when available and CPU otherwise; explicit `cpu` stays on CPU. CUDA is unsupported by the canonical local adapters. Semantic model names remain separate from optional provisioned model paths. Changing embedding identity requires a compatible rebuilt index.

There is one public research workflow. Retired routing, decomposer and web-provider sections are rejected. The UI uses the default file; CLI/evaluation expose `--config`. There is no `WORKBENCH_CONFIG` environment override. Full configuration, model identity and resolved snapshot participate in application cache identity.

Launchers set offline model-cache and telemetry flags. Runtime clients reject remote endpoints and redirects and ignore HTTP proxies. Source URLs are metadata, not runtime fetch requests. See [setup](setup.md) and [observability](observability.md).
