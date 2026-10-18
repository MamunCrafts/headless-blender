#!/usr/bin/env bash
# Container entrypoint for RunPod.
#   PUBLIC_KEY    set by RunPod from your account SSH key; enables sshd
#   BLEND_FILE    if set, render it on startup (e.g. /workspace/scene.blend)
#   OUTPUT_DIR    where frames are written (default /workspace/output)
#   RENDER_ARGS   extra render.py args, e.g. "--frames 1-250 --samples 256 --denoise"
#   EXIT_ON_DONE  1 = stop the container after rendering, 0 = keep it running
set -u

if [ -n "${PUBLIC_KEY:-}" ]; then
    mkdir -p /root/.ssh /run/sshd
    echo "$PUBLIC_KEY" >> /root/.ssh/authorized_keys
    chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys
    ssh-keygen -A >/dev/null
    /usr/sbin/sshd
    echo "[start.sh] sshd started"
fi

nvidia-smi 2>/dev/null || echo "[start.sh] WARNING: nvidia-smi not available"

if [ -n "${BLEND_FILE:-}" ]; then
    echo "[start.sh] Rendering $BLEND_FILE -> $OUTPUT_DIR"
    # shellcheck disable=SC2086
    blender -b --python-exit-code 1 -P /app/render.py -- \
        --blend "$BLEND_FILE" --output "$OUTPUT_DIR" $RENDER_ARGS
    status=$?
    echo "[start.sh] Render finished with exit code $status"
    if [ "${EXIT_ON_DONE:-0}" = "1" ]; then
        exit "$status"
    fi
fi

# Keep the pod alive for SSH / web terminal use
exec sleep infinity
