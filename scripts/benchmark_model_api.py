"""Small reproducible streaming benchmark; synthetic inputs, no database.

This measures a warm API, not semantic accuracy or production capacity. Output
is intentionally truncated at the requested length to compare decode rates.
Run from the same directory as probe_model_api.py. No prompt or key is logged.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import time
import urllib.request
from pathlib import Path

from probe_model_api import NoRedirect, endpoint, synthetic_image


def request(args, key, name, image=False):
    content = [
        {
            "type": "text",
            "text": (
                "请详细介绍如何检查信息来源、核对时间与数字，并保存可追溯的证据。"
                "分条持续展开，每条包含具体例子。" + ("也请描述附图的形状、颜色与位置。" if image else "")
            ),
        }
    ]
    if image:
        content.append({"type": "image_url", "image_url": {"url": synthetic_image()}})
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": args.tokens,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
        "ignore_eos": True,
    }
    raw = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        endpoint(args.base_url) + "/chat/completions",
        data=raw,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    result = {
        "name": name,
        "images": int(image),
        "input_bytes": len(raw),
        "started_utc": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    start = time.monotonic()
    first = last = None
    usage = finish = None
    chunks = 0
    try:
        with opener.open(req, timeout=args.timeout) as response:
            for line in response:
                if not line.startswith(b"data: ") or line.strip() == b"data: [DONE]":
                    continue
                item = json.loads(line[6:])
                if item.get("error"):
                    raise ValueError("stream_error")
                now = time.monotonic()
                usage = item.get("usage") or usage
                for choice in item.get("choices", []):
                    delta = choice.get("delta", {})
                    if delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning"):
                        first = now if first is None else first
                        last = now
                        chunks += 1
                    finish = choice.get("finish_reason") or finish
        seconds = time.monotonic() - start
        if not usage or first is None or usage.get("completion_tokens") != args.tokens:
            raise ValueError("incomplete_usage_or_unexpected_output_length")
        result.update(
            status="completed",
            usage=usage,
            finish_reason=finish,
            seconds=seconds,
            time_to_first_content_seconds=first - start,
            output_tokens_per_second_e2e=usage["completion_tokens"] / seconds,
            # SSE chunks can contain multiple tokens; this is an approximation.
            output_tokens_per_second_after_first_approx=(usage["completion_tokens"] - 1) / (last - first)
            if last > first
            else None,
            content_chunks=chunks,
        )
    except (OSError, ValueError) as exc:
        result.update(status="failed", error=type(exc).__name__, seconds=time.monotonic() - start)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001/v1")
    parser.add_argument("--model", default="hangzhi-qwen38")
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tokens", type=int, default=512)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    if not 128 <= args.tokens <= 16000:
        parser.error("tokens must be between 128 and 16000")
    key = args.key_file.read_text().strip()
    if not key:
        parser.error("empty key file")
    runs = []
    report = {
        "method": "warm streaming API; ignore_eos=true; intentional length finish; not quality acceptance",
        "model": args.model,
        "requested_output_tokens": args.tokens,
        "runs": runs,
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    for image in (False, True):
        for repetition in (1, 2):
            result = request(args, key, f"{'vision' if image else 'text'}-single-{repetition}", image)
            runs.append(result)
            save()
            print(json.dumps(result, ensure_ascii=False), flush=True)
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request, args, key, f"text-concurrent-{i}") for i in (1, 2)]
        pair = [future.result() for future in futures]
    elapsed = time.monotonic() - start
    runs.extend(pair)
    report["concurrency_2"] = {
        "seconds": elapsed,
        "aggregate_output_tokens_per_second_e2e": sum(
            row.get("usage", {}).get("completion_tokens", 0) for row in pair
        )
        / elapsed,
    }
    save()
    print(json.dumps(report["concurrency_2"]), flush=True)
    return 0 if all(row["status"] == "completed" for row in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
