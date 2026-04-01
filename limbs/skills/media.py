"""
Media Skill - Video and image generation for CyberGrunt 2.0
"""

import os
import json
import time
import urllib.request
import urllib.error
import logging
from limbs.hub import limb
import messaging

log = logging.getLogger("agent")

def _video_output_path(workspace):
    os.makedirs(os.path.join(workspace, "files"), exist_ok=True)
    return os.path.join(workspace, "files", "video_%d.mp4" % int(time.time()))

@limb("generate_video", "Generate a short video from a text prompt (using external video generation API). "
      "Async task, typically takes 2-5 minutes.",
      {"prompt": {"type": "string", "description": "Video content description"},
       "size": {"type": "string", "description": "Video resolution, default 1280x720"}},
      ["prompt"])
def tool_generate_video(args, ctx):
    from limbs.hub import _extra_config
    video_cfg = _extra_config.get("video_api", {})
    api_key = video_cfg.get("api_key", "")
    if not api_key:
        return "[error] video_api.api_key not configured"
    
    api_base = video_cfg.get("api_base", "https://api.video-generation.example.com/v1")
    model = video_cfg.get("model", "video-generation-model")

    body = json.dumps({
        "model": model,
        "prompt": args["prompt"],
        "size": args.get("size", "1280x720"),
    }).encode("utf-8")
    
    req = urllib.request.Request(
        "%s/videos/generations" % api_base, data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer %s" % api_key},
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            task = json.loads(resp.read())
            task_id = task.get("id", "")
            return f"Video generation task submitted: {task_id}. This is an async process."
    except Exception as e:
        return f"[error] Video generation failed: {e}"
