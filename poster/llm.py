"""Headless Claude Code on the owner's subscription, with structured JSON output."""

import asyncio
import json
import os

from poster import config


class LLMError(RuntimeError):
    pass


async def run(prompt_name: str, task: str, schema: dict, *, timeout: int) -> dict:
    """One model run with web search and fetch only; returns the structured answer."""
    config.RUN_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        "claude",
        "-p",
        "--model",
        config.MODEL,
        "--setting-sources",
        "project",  # skip the owner's own CLAUDE.md, hooks and plugins
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
        "--system-prompt",
        (config.PROMPTS / f"{prompt_name}.md")
        .read_text()
        .replace("{channel_name}", config.CHANNEL_NAME)
        .replace("{channel_url}", config.CHANNEL_URL),
        "--json-schema",
        json.dumps(schema),
        "--tools",
        "WebSearch,WebFetch",
        "--allowedTools",
        "WebSearch",
        "WebFetch",
        "--strict-mcp-config",
        "--",
        task,
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=config.RUN_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(os.environ),
        limit=16 * 1024 * 1024,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise LLMError(f"{prompt_name}: timed out after {timeout}s")
    # The whole run (searches, pages, the answer) is kept for review.
    (config.RUN_DIR / f"{prompt_name}-last.jsonl").write_bytes(out)
    result = None
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "result":
            result = event
    if result is None:
        raise LLMError(f"{prompt_name}: exit {proc.returncode}: {err.decode()[-500:]}")
    if result.get("is_error") or result.get("structured_output") is None:
        raise LLMError(f"{prompt_name}: {str(result.get('result'))[:500]}")
    return result["structured_output"]
