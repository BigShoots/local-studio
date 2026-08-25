# Linux Intel XPU desktop

Local Studio can run as an AppImage on Linux and launch vLLM Docker targets on
Intel GPUs through Level Zero.

## Build and install

Install the repository dependencies, build the AppImage, and install it for the
current user:

```bash
npm run setup
CSC_IDENTITY_AUTO_DISCOVERY=false npm run desktop:dist:linux
bash scripts/install-desktop-app.sh stable
```

The installer places the executable at `~/.local/bin/local-studio` and creates
a desktop entry under `~/.local/share/applications`. The desktop entry uses the
AppImage extract-and-run fallback, so FUSE 2 is not required.

## Controller

Install the controller as a persistent user service and point it at the model
directory:

```bash
LOCAL_STUDIO_MODELS_DIR=/path/to/models bash scripts/install-controller.sh
```

On a host with both NVIDIA and Intel GPUs, set
`LOCAL_STUDIO_GPU_SMI_TOOL=intel-sysfs` in `.env` to select the Intel device.
The installer detects an Intel Arc Pro B70 and sets this automatically.

Intel vLLM Docker launches use the official XPU image, map and mount `/dev/dri`,
use host IPC, select the leased Level Zero device, and retain the vLLM compile
cache in the `local-studio-vllm-xpu-cache` Docker volume. A custom image can be
selected in a recipe for model-specific patches.

## Qwen3.8 27B on Arc Pro B70

`integrations/intel-b70/Dockerfile.qwen38-mtp4` builds on the pinned vLLM XPU
image and applies the Qwen3.8 MTP boundary and draft-INT4 patches from the Intel
Arc Pro B70 inference cookbook. Build it with the cookbook as the Docker build
context:

```bash
docker build \
  -f integrations/intel-b70/Dockerfile.qwen38-mtp4 \
  -t local-studio/vllm-openai-xpu-b70:qwen38-mtp4 \
  /path/to/intel-arc-pro-b70-inference-cookbook
```

Create a vLLM Docker recipe that selects this image and the local GPTQ model.
The validated long-context profile uses native 262,144 context, FP8 KV cache,
MTP4, draft-side INT4, one sequence, an 8,192-token scheduler batch, and a GPU
memory utilization selected for the card's available VRAM.

DeepSeekHarness model discovery and automatic load/evict behavior are described
in [`integrations/deepseekharness/README.md`](../integrations/deepseekharness/README.md).
