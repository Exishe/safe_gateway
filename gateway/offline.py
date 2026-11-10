"""Tell the Hugging Face libraries to stay offline, before anything imports them (review M7).

`huggingface_hub`, `transformers` and `datasets` read these variables when they are
imported, not when they are used. Presidio imports transformers and huggingface_hub
on its own, before the GLiNER loader runs, so the process entry points call
`force_offline()` first; `gliner_ner.load_gliner` still sets them as a fallback for
callers that import the engine directly. Models are fetched only by
`safeguard models pull`, which uses its own pinned client (ADR 0006, ADR 0015).
"""

import os

OFFLINE_VARIABLES = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")


def force_offline() -> None:
    """Set every offline variable to 1, whatever the environment said."""
    for name in OFFLINE_VARIABLES:
        os.environ[name] = "1"
