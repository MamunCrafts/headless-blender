# Health Check (RunPod Serverless)

How to confirm a headless-blender serverless endpoint is up, has workers, and can render on the GPU.

> **Prerequisite:** the endpoint must run an image built with `handler.py`
> (the default `CMD`). Images built before that run `start.sh` and
> `sleep infinity`, so their workers never pick up jobs.

Set these first:

```bash
export RUNPOD_API_KEY=<your api key>
export ENDPOINT_ID=yag8advzl9e2y9
```

## 1. Endpoint health

`GET https://api.runpod.ai/v2/$ENDPOINT_ID/health`

```bash
curl -s https://api.runpod.ai/v2/$ENDPOINT_ID/health \
  -H "Authorization: Bearer $RUNPOD_API_KEY"
```

Example response:

```json
{
  "jobs":    { "completed": 12, "failed": 0, "inProgress": 0, "inQueue": 0, "retried": 0 },
  "workers": { "idle": 1, "initializing": 0, "ready": 1, "running": 0, "throttled": 0, "unhealthy": 0 }
}
```

| Field                   | Healthy                         | Problem                                                                    |
|-------------------------|---------------------------------|----------------------------------------------------------------------------|
| `workers.unhealthy`     | `0`                             | Workers crash on start: image broken, or the handler raises on import      |
| `workers.throttled`     | `0`                             | No GPUs of the chosen type available; add more GPU types to the endpoint   |
| `workers.initializing`  | Drops to `0` after cold start   | Stuck: image pull failing or too large; check the worker logs              |
| `jobs.inQueue`          | Goes back to `0`                | Keeps growing: no workers taking jobs (max workers `0`, or no handler)     |
| `jobs.failed`           | Not increasing                  | Check the failed job's `error` with `/status` (section 4)                  |

`idle: 0` with `running: 0` is normal when min workers is `0`; the first job triggers a cold start.

## 2. Worker logs

In the RunPod console, open the endpoint, then **Workers**, then a worker's **Logs**.
During a job you should see the handler's command followed by Blender's output:

```
[handler] blender -b --factory-startup --python-exit-code 1 -P /app/render.py -- --output ...
[render.py] Blender 4.2.3 ...
[render.py]   device: NVIDIA GeForce RTX 4090 (OPTIX) ENABLED
[render.py] Cycles using GPU via OPTIX
```

A Python traceback mentioning `runpod` at startup means the image was built without the `runpod` package.

## 3. Smoke-test render job

`POST https://api.runpod.ai/v2/$ENDPOINT_ID/runsync`

The payload renders Blender's built-in default scene (cube, camera, light), so no `.blend` file is needed:

```json
{
  "input": {
    "frames": "1",
    "samples": 16,
    "resolution": "320x240",
    "device": "AUTO"
  }
}
```

Leaving out `blend` and `blend_url` makes the handler render the default scene.
To render your own file, add `"blend": "/runpod-volume/scene.blend"` (network volume)
or `"blend_url": "https://..."`. Any other `render.py` option can be added the same way,
e.g. `"denoise": true`, `"format": "JPEG"`.

```bash
curl -s -X POST https://api.runpod.ai/v2/$ENDPOINT_ID/runsync \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d @healthcheck.json
```

Expected response:

```json
{
  "id": "sync-xxxxxxxx",
  "status": "COMPLETED",
  "delayTime": 2140,
  "executionTime": 5321,
  "output": {
    "backend": "OPTIX",
    "output_dir": "/tmp/sync-xxxxxxxx",
    "frames": ["/tmp/sync-xxxxxxxx/frame_0001.png"],
    "elapsed": 4.8,
    "images": { "frame_0001.png": "iVBORw0KGgo..." }
  }
}
```

- Without a network volume, frames are written to `/tmp` (lost when the worker stops)
  and returned base64-encoded in `images`. Decode one with:
  `jq -r '.output.images["frame_0001.png"]' response.json | base64 -d > frame.png`
- With a network volume, frames go to `/runpod-volume/output/<job id>/` and `images`
  is omitted unless the input sets `"return_base64": true`.
- On failure the output is `{"error": "...", "log_tail": "..."}`.

`delayTime` and `executionTime` are in milliseconds. A cold start mostly adds to `delayTime`.

## 4. Job status (async or timed-out jobs)

`/runsync` returns `IN_PROGRESS` with an `id` if the job takes longer than the sync wait.
Poll it:

```bash
curl -s https://api.runpod.ai/v2/$ENDPOINT_ID/status/<job id> \
  -H "Authorization: Bearer $RUNPOD_API_KEY"
```

| `status`       | Meaning                                                       |
|----------------|---------------------------------------------------------------|
| `IN_QUEUE`     | Waiting for a worker                                          |
| `IN_PROGRESS`  | Rendering                                                     |
| `COMPLETED`    | Done; read `output`                                           |
| `FAILED`       | Read `error`; the worker logs have the full traceback         |
| `TIMED_OUT`    | Exceeded the endpoint's execution timeout                     |

## 5. Pass / fail

| Check            | Pass                                       | Fail: what to look at                                         |
|------------------|--------------------------------------------|---------------------------------------------------------------|
| Endpoint reachable | `/health` returns JSON                   | `401`: API key. `404`: endpoint ID                            |
| Workers healthy  | `unhealthy: 0`, `throttled: 0`             | Worker logs; GPU type availability                            |
| Job completes    | `status: COMPLETED`                        | `error` from `/status`; worker logs                           |
| GPU used         | `output.backend` is `OPTIX` or `CUDA`      | `No GPU found` in logs: GPU type or driver                    |
| Frame written    | `output.frames` is not empty               | `log_tail` in the output; worker logs                         |

## 6. Clean up

A failed or stuck job can be cancelled:

```bash
curl -s -X POST https://api.runpod.ai/v2/$ENDPOINT_ID/cancel/<job id> \
  -H "Authorization: Bearer $RUNPOD_API_KEY"
```

Serverless bills only while workers run. Keep **min workers** at `0` for test endpoints so idle workers don't bill.
