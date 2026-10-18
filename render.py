"""
Headless Blender render script for GPU pods (e.g. RunPod).

Usage:
    blender -b --python-exit-code 1 -P render.py -- --blend /workspace/scene.blend [options]

Examples:
    # Single frame, Cycles on all GPUs (OptiX, falls back to CUDA)
    blender -b --python-exit-code 1 -P render.py -- \
        --blend /workspace/scene.blend --output /workspace/out --frames 1

    # Frame range with overrides, resumable (existing frames are skipped)
    blender -b --python-exit-code 1 -P render.py -- \
        --blend /workspace/scene.blend --output /workspace/out \
        --frames 1-250 --samples 256 --resolution 1920x1080 --denoise

    # MP4 video of a range
    blender -b --python-exit-code 1 -P render.py -- \
        --blend /workspace/scene.blend --output /workspace/out --frames 1-120 --format FFMPEG

Everything after "--" is parsed by this script; everything before it goes to Blender.
"""

import argparse
import os
import sys
import time

import bpy


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    p = argparse.ArgumentParser(prog="render.py", description="Headless Blender GPU render")
    p.add_argument("--blend", help="Path to .blend file (omit if passed to blender before -P)")
    p.add_argument("--output", default="/workspace/output", help="Output directory")
    p.add_argument("--prefix", default="frame_", help="Output filename prefix")
    p.add_argument("--frames", help="Frames to render: '10', '1-250', or '1,5,10-20'. "
                                    "Default: the scene's frame range")
    p.add_argument("--scene", help="Scene name (default: active scene)")
    p.add_argument("--camera", help="Camera object name (default: scene camera)")
    p.add_argument("--engine", default="CYCLES", choices=["CYCLES", "EEVEE", "WORKBENCH", "KEEP"],
                   help="Render engine; KEEP uses whatever the .blend file has")
    p.add_argument("--device", default="AUTO", choices=["AUTO", "OPTIX", "CUDA", "HIP", "ONEAPI", "CPU"],
                   help="Cycles compute backend; AUTO tries OPTIX then CUDA then HIP")
    p.add_argument("--use-cpu-too", action="store_true", help="Also use CPU alongside GPUs (Cycles)")
    p.add_argument("--cpu-fallback", action="store_true",
                   help="Render on CPU if no GPU is found instead of failing")
    p.add_argument("--samples", type=int, help="Cycles render samples")
    p.add_argument("--denoise", action="store_true", help="Enable Cycles denoising")
    p.add_argument("--resolution", help="WIDTHxHEIGHT, e.g. 1920x1080")
    p.add_argument("--percentage", type=int, help="Resolution percentage (1-100+)")
    p.add_argument("--format", default="PNG",
                   choices=["PNG", "JPEG", "OPEN_EXR", "OPEN_EXR_MULTILAYER", "TIFF", "FFMPEG"],
                   help="Output file format")
    p.add_argument("--overwrite", action="store_true", help="Re-render frames that already exist")
    p.add_argument("--threads", type=int, help="CPU thread count (default: auto)")
    return p.parse_args(argv)


def log(msg):
    print(f"[render.py] {msg}", flush=True)


def parse_frames(spec, scene):
    if not spec:
        return list(range(scene.frame_start, scene.frame_end + 1, scene.frame_step))
    frames = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            frames.extend(range(start, end + 1))
        elif part:
            frames.append(int(part))
    return sorted(set(frames))


def setup_gpu(device_type, use_cpu_too):
    """Enable GPU devices in Cycles preferences. Returns the backend used, or None."""
    prefs = bpy.context.preferences.addons["cycles"].preferences

    if device_type == "CPU":
        return None

    candidates = ["OPTIX", "CUDA", "HIP", "ONEAPI"] if device_type == "AUTO" else [device_type]
    for backend in candidates:
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue  # backend not supported by this Blender build
        if hasattr(prefs, "refresh_devices"):
            prefs.refresh_devices()
        else:
            prefs.get_devices()

        gpus = [d for d in prefs.devices if d.type == backend]
        if not gpus:
            continue

        for d in prefs.devices:
            d.use = d.type == backend or (use_cpu_too and d.type == "CPU")
            log(f"  device: {d.name} ({d.type}) {'ENABLED' if d.use else 'disabled'}")
        return backend

    return None


def set_engine(scene, engine):
    if engine == "KEEP":
        return
    if engine == "EEVEE":
        # 4.2-4.x calls it BLENDER_EEVEE_NEXT; earlier and 5.x use BLENDER_EEVEE
        available = scene.render.bl_rna.properties["engine"].enum_items.keys()
        engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in available else "BLENDER_EEVEE"
    elif engine == "WORKBENCH":
        engine = "BLENDER_WORKBENCH"
    scene.render.engine = engine


def configure_scene(scene, args):
    set_engine(scene, args.engine)

    if args.camera:
        cam = bpy.data.objects.get(args.camera)
        if cam is None or cam.type != "CAMERA":
            raise ValueError(f"Camera '{args.camera}' not found")
        scene.camera = cam
    if scene.camera is None:
        raise ValueError("Scene has no active camera")

    r = scene.render
    if args.resolution:
        w, h = (int(x) for x in args.resolution.lower().split("x"))
        r.resolution_x, r.resolution_y = w, h
    if args.percentage:
        r.resolution_percentage = args.percentage
    if args.threads:
        r.threads_mode = "FIXED"
        r.threads = args.threads

    r.image_settings.file_format = args.format
    if args.format == "FFMPEG":
        r.ffmpeg.format = "MPEG4"
        r.ffmpeg.codec = "H264"
        r.ffmpeg.constant_rate_factor = "HIGH"
    r.use_file_extension = True
    r.use_overwrite = args.overwrite
    r.use_placeholder = False
    # Keeps scene data on the GPU between frames; big speedup for animations
    r.use_persistent_data = True

    os.makedirs(args.output, exist_ok=True)
    r.filepath = os.path.join(os.path.abspath(args.output), args.prefix + "####")

    if scene.render.engine == "CYCLES":
        if args.samples:
            scene.cycles.samples = args.samples
        if args.denoise:
            scene.cycles.use_denoising = True
            scene.cycles.denoiser = "OPENIMAGEDENOISE"


def main():
    args = parse_args()
    t0 = time.time()

    log(f"Blender {bpy.app.version_string}")

    if args.blend:
        if not os.path.isfile(args.blend):
            raise FileNotFoundError(args.blend)
        log(f"Opening {args.blend}")
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))

    scene = bpy.data.scenes[args.scene] if args.scene else bpy.context.scene
    if args.scene:
        bpy.context.window.scene = scene

    configure_scene(scene, args)

    if scene.render.engine == "CYCLES":
        backend = setup_gpu(args.device, args.use_cpu_too)
        if backend:
            scene.cycles.device = "GPU"
            log(f"Cycles using GPU via {backend}")
        elif args.device == "CPU" or args.cpu_fallback:
            scene.cycles.device = "CPU"
            log("Cycles using CPU")
        else:
            raise RuntimeError("No GPU found. Check the pod has a GPU / NVIDIA drivers, "
                               "or pass --cpu-fallback / --device CPU")

    frames = parse_frames(args.frames, scene)
    if not frames:
        raise ValueError("No frames to render")

    r = scene.render
    log(f"Scene '{scene.name}', engine {r.engine}, "
        f"{r.resolution_x}x{r.resolution_y} @ {r.resolution_percentage}%, "
        f"{len(frames)} frame(s), output {args.output}")

    if args.format == "FFMPEG":
        # Video must be rendered as one contiguous animation
        scene.frame_start, scene.frame_end = frames[0], frames[-1]
        bpy.ops.render.render(animation=True, scene=scene.name)
    else:
        template = r.filepath
        for i, frame in enumerate(frames, 1):
            out_path = r.frame_path(frame=frame)
            if not args.overwrite and os.path.exists(out_path):
                log(f"[{i}/{len(frames)}] frame {frame} exists, skipping")
                continue
            ft = time.time()
            scene.frame_set(frame)
            # write_still doesn't expand "####", so give it the concrete path
            r.filepath = os.path.splitext(out_path)[0]
            bpy.ops.render.render(write_still=True, scene=scene.name)
            r.filepath = template
            log(f"[{i}/{len(frames)}] frame {frame} -> {out_path} ({time.time() - ft:.1f}s)")

    log(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
