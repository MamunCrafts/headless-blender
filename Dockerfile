# Headless Blender for RunPod GPU pods.
#
# Build (from an Apple Silicon Mac you must target amd64):
#   docker buildx build --platform linux/amd64 -t <dockerhub-user>/headless-blender:4.2 --push .
#
# Blender ships its own CUDA/OptiX kernels; the NVIDIA driver is injected by the
# host container runtime, so a plain Ubuntu base is enough (no CUDA toolkit needed).
FROM ubuntu:22.04

ARG BLENDER_VERSION=4.2.3
ARG BLENDER_SERIES=4.2

ENV DEBIAN_FRONTEND=noninteractive \
    # "all" includes graphics, which provides libnvoptix for OptiX and EGL for EEVEE
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=all \
    PATH="/opt/blender:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates wget xz-utils \
        libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxext6 \
        libxkbcommon0 libxkbcommon-x11-0 libsm6 libice6 \
        libgl1 libegl1 libglx0 libgomp1 \
        openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    # Host keys must be unique per pod; start.sh generates them at runtime
    && rm -f /etc/ssh/ssh_host_*

RUN wget -q "https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
        -O /tmp/blender.tar.xz \
    && mkdir -p /opt/blender \
    && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
    && rm /tmp/blender.tar.xz \
    && blender -b --version

WORKDIR /app
COPY render.py start.sh /app/
RUN chmod +x /app/start.sh

# Default render settings; override in the RunPod template's environment variables
ENV BLEND_FILE="" \
    OUTPUT_DIR=/workspace/output \
    RENDER_ARGS="" \
    EXIT_ON_DONE=0

CMD ["/app/start.sh"]
