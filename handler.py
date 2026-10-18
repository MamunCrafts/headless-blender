"""
RunPod Serverless handler: runs render.py in Blender for each job.

Job input (all optional):
    blend         path to a .blend on the network volume, e.g. /runpod-volume/scene.blend
    blend_url     http(s) URL to download the .blend from (used if blend is not set)
                  with neither, Blender's default scene is rendered (health check)
    frames, samples, resolution, percentage, engine, device, format,
    scene, camera, prefix, threads            passed to render.py as --<name> <value>
    denoise, overwrite, use_cpu_too, cpu_fallback   passed as flags when true
    return_base64 include rendered files in the response as base64
                  (default: true when no network volume is attached)

Output:
    {"backend": "OPTIX", "output_dir": "...", "frames": [...], "elapsed": 5.3, "images": {...}}
or on failure:
    {"error": "...", "log_tail": "..."}
"""

import base64
import os
import re
import subprocess
import tempfile
import time
import urllib.request

import runpod

RENDER_SCRIPT = "/app/render.py"
VOLUME = "/runpod-volume"

VALUE_ARGS = ["frames", "samples", "resolution", "percentage", "engine", "device", "format",
              "scene", "camera", "prefix", "threads"]
FLAG_ARGS = ["denoise", "overwrite", "use_cpu_too", "cpu_fallback"]


def build_command(inp, blend_path, output_dir):
    cmd = ["blender", "-b"]
    if blend_path is None:
        # Default cube scene; render.py needs a camera, which it has
        cmd.append("--factory-startup")
    cmd += ["--python-exit-code", "1", "-P", RENDER_SCRIPT, "--", "--output", output_dir]
    if blend_path:
        cmd += ["--blend", blend_path]
    for key in VALUE_ARGS:
        if inp.get(key) is not None:
            cmd += ["--" + key.replace("_", "-"), str(inp[key])]
    for key in FLAG_ARGS:
        if inp.get(key):
            cmd.append("--" + key.replace("_", "-"))
    return cmd


def handler(job):
    inp = job.get("input") or {}
    t0 = time.time()
    has_volume = os.path.isdir(VOLUME)

    output_root = os.path.join(VOLUME, "output") if has_volume else tempfile.gettempdir()
    output_dir = os.path.join(output_root, job["id"])

    blend_path = inp.get("blend")
    if not blend_path and inp.get("blend_url"):
        blend_path = os.path.join(tempfile.gettempdir(), f"{job['id']}.blend")
        print(f"[handler] Downloading {inp['blend_url']}", flush=True)
        try:
            urllib.request.urlretrieve(inp["blend_url"], blend_path)
        except Exception as e:
            return {"error": f"Failed to download blend_url: {e}"}

    cmd = build_command(inp, blend_path, output_dir)
    print(f"[handler] {' '.join(cmd)}", flush=True)

    # Stream Blender's output to the worker log and keep it for the response
    lines = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    proc.wait()

    if proc.returncode != 0:
        errors = [l.strip() for l in lines if "[render.py] ERROR:" in l]
        return {
            "error": errors[-1] if errors else f"Blender exited with code {proc.returncode}",
            "log_tail": "".join(lines[-40:]),
        }

    log = "".join(lines)
    m = re.search(r"Cycles using (?:GPU via (\w+)|(CPU))", log)
    backend = (m.group(1) or m.group(2)) if m else None

    frames = sorted(os.path.join(output_dir, f) for f in os.listdir(output_dir)) \
        if os.path.isdir(output_dir) else []

    result = {
        "backend": backend,
        "output_dir": output_dir,
        "frames": frames,
        "elapsed": round(time.time() - t0, 1),
    }

    if inp.get("return_base64", not has_volume):
        result["images"] = {}
        for path in frames:
            with open(path, "rb") as f:
                result["images"][os.path.basename(path)] = base64.b64encode(f.read()).decode()

    return result


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
