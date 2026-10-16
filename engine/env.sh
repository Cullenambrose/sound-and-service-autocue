# Source from repo root:  source engine/env.sh
# Override PROOF if your analysis working tree lives elsewhere.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROOF="${PROOF:-$ROOT}"
export HF_HOME="${HF_HOME:-$PROOF/cache/hf}"
export TORCH_HOME="${TORCH_HOME:-$PROOF/cache/torch}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$PROOF/cache/xdg}"
export UV_LINK_MODE=copy
export HF_HUB_DISABLE_TELEMETRY=1
