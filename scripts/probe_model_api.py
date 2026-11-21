"""Database-free acceptance probe; stdlib only, synthetic images, no cloud fallback.

Run: python scripts/probe_model_api.py --output .local/model-api/probe.json
Credentials: HANGZHI_UPSTREAM_API_KEY or --key-file (never a command-line key).
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def endpoint(value):
    url = urllib.parse.urlsplit(value)
    address = ipaddress.ip_address(url.hostname or "")
    private = any(
        address in ipaddress.ip_network(n)
        for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
    )
    if (
        not (address.is_loopback or private)
        or url.scheme not in ({"http", "https"} if address.is_loopback else {"https"})
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path.rstrip("/") != "/v1"
        or url.port == 0
    ):
        raise ValueError("Use a numeric loopback HTTP(S) /v1 URL or private HTTPS /v1 URL")
    return value.rstrip("/")


def synthetic_image(reverse=False):
    """384x256 RGB PNG: a square on the left and a circle on the right, no text."""
    width, height = 384, 256
    red, blue = (240, 20, 20), (20, 50, 240)
    left, right = (blue, red) if reverse else (red, blue)
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            color = left if 35 <= x < 145 and 70 <= y < 180 else (255, 255, 255)
            if (x - 275) ** 2 + (y - 125) ** 2 <= 55**2:
                color = right
            rows.extend(color)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(raw).decode()


class Probe:
    def __init__(self, base_url, key, model, timeout):
        self.base_url, self.key, self.model, self.timeout = endpoint(base_url), key, model, timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.results = []
        self.requests = []

    def request(self, method, path, payload=None, *, key=None):
        token = self.key if key is None else key
        raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(self.base_url + path, data=raw, headers=headers, method=method)
        start = time.monotonic()
        status, data = None, None
        transport_error = None
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = response.status
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                data = json.loads(exc.read())
            except (ValueError, UnicodeDecodeError):
                data = {"error": "non_json_error"}
        except (OSError, urllib.error.URLError) as exc:
            transport_error = type(exc).__name__
            raise
        finally:
            self.requests.append(
                {
                    "method": method,
                    "path": path,
                    "status": status,
                    "input_bytes": len(raw or b""),
                    "images": sum(
                        part.get("type") == "image_url"
                        for message in (payload or {}).get("messages", [])
                        if isinstance(message.get("content"), list)
                        for part in message["content"]
                    ),
                    "seconds": round(time.monotonic() - start, 3),
                    "response": data,
                    "transport_error": transport_error,
                }
            )
        return status, data

    def chat(self, messages, **kwargs):
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 512,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
            **kwargs,
        }
        status, data = self.request("POST", "/chat/completions", payload)
        assert status == 200, f"chat status={status}"
        assert data.get("model") == self.model, "model identity mismatch"
        usage = data.get("usage", {})
        assert all(
            type(usage.get(k)) is int and usage[k] > 0 for k in ("prompt_tokens", "completion_tokens")
        ), "invalid usage"
        assert usage.get("total_tokens") == usage["prompt_tokens"] + usage["completion_tokens"], (
            "invalid total_tokens"
        )
        assert len(data.get("choices", [])) == 1, "invalid choices"
        choice = data["choices"][0]
        assert choice.get("finish_reason") in {"stop", "tool_calls"}, (
            f"unfinished: {choice.get('finish_reason')}"
        )
        message = choice["message"]
        assert message.get("role") == "assistant", "invalid assistant role"
        assert not message.get("refusal"), "unexpected refusal"
        assert not (message.get("reasoning") or message.get("reasoning_content")), "thinking was not disabled"
        assert not any(tag in (message.get("content") or "") for tag in ("<think>", "</think>")), (
            "reasoning leaked into content"
        )
        if choice["finish_reason"] == "tool_calls":
            assert message.get("tool_calls"), "tool_calls finish reason without calls"
        else:
            assert isinstance(message.get("content"), str) and message["content"].strip(), (
                "empty final content"
            )
            assert not message.get("tool_calls"), "tool calls with stop finish reason"
        return message

    def check(self, name, function):
        start = len(self.requests)
        try:
            function()
            result = {"name": name, "status": "passed"}
        except Exception as exc:  # noqa: BLE001 - preserve independent failure results without request dumps.
            # Avoid request/credential dumps from network exceptions.
            detail = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
            result = {"name": name, "status": "failed", "detail": detail[:300]}
        result["requests"] = self.requests[start:]
        self.results.append(result)
        print(
            json.dumps({k: v for k, v in result.items() if k != "requests"}, ensure_ascii=False), flush=True
        )

    @staticmethod
    def user(text, *images):
        if not images:
            return [{"role": "user", "content": text}]
        content = [{"type": "text", "text": text}]
        content.extend({"type": "image_url", "image_url": {"url": img}} for img in images)
        return [{"role": "user", "content": content}]

    def models(self):
        status, data = self.request("GET", "/models")
        assert status == 200 and self.model in [v.get("id") for v in data.get("data", [])], (
            "served model missing"
        )

    def auth(self):
        for key in ("", "invalid-synthetic-test-token"):
            for method, path, payload in (
                ("GET", "/models", None),
                (
                    "POST",
                    "/chat/completions",
                    {"model": self.model, "messages": self.user("鉴权检查"), "max_tokens": 8},
                ),
            ):
                status, _ = self.request(method, path, payload, key=key)
                assert status in {401, 403}, f"unauthorized request accepted ({status})"

    def text(self):
        reply = self.chat(self.user("请用中文简短回答：中国的首都是哪座城市？"))
        assert "北京" in reply["content"], "Chinese answer incorrect"

    def vision(self, reverse=False):
        reply = self.chat(
            self.user("描述图片中两个形状的颜色、形状及左右位置。请用中文回答。", synthetic_image(reverse))
        )
        text = reply["content"]
        assert all(
            any(k in text for k in group)
            for group in (("红", "red"), ("蓝", "blue"), ("方", "square"), ("圆", "circle"))
        ), "visual shapes/colors missing"
        square = "蓝" if reverse else "红"
        assert any(
            s in text
            for s in (
                square + "色正方形",
                square + "色方块",
                square + "色方形",
                square + "色的正方形",
                square + "色的方形",
            )
        ), "square color incorrect"

    def visual_json(self, multi=False, strict=False):
        expected = {"left_color": "red", "right_color": "blue", "relation": "left_of", "image_count": 1}
        images = [synthetic_image()]
        prompt = (
            "观察上传的图片，输出JSON：left_color/right_color是左右两个形状的英文颜色名，relation为left_of。"
            "image_count指上传的图片文件张数（不是图片里的形状数量），必须是整数。"
        )
        if multi:
            images.append(synthetic_image(True))
            expected = {"first_left_color": "red", "second_left_color": "blue", "image_count": 2}
            prompt = "按上传顺序观察两张图片。输出JSON：first_left_color和second_left_color为各图左侧方形的英文颜色名，image_count为整数。"
        properties = {k: {"type": "integer" if type(v) is int else "string"} for k, v in expected.items()}
        schema = {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
        fmt = (
            {"type": "json_schema", "json_schema": {"name": "scene", "strict": True, "schema": schema}}
            if strict
            else {"type": "json_object"}
        )
        reply = self.chat(self.user(prompt, *images), response_format=fmt)
        value = json.loads(reply["content"])
        assert value == expected and all(type(value[k]) is type(v) for k, v in expected.items()), (
            "visual JSON values/types incorrect"
        )

    def tools(self, json_mode=False):
        messages = self.user(
            '必须调用multiply工具计算12乘7，不能自行心算。工具返回后只输出JSON {"result":整数}。'
        )
        tool = {
            "type": "function",
            "function": {
                "name": "multiply",
                "description": "Multiply two integers exactly.",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
            },
        }
        extra = {"response_format": {"type": "json_object"}} if json_mode else {}
        reply = self.chat(messages, tools=[tool], tool_choice="required", **extra)
        calls = reply.get("tool_calls") or []
        assert len(calls) == 1, "expected one real tool call"
        call = calls[0]
        assert call.get("id") and call.get("type") == "function" and call["function"]["name"] == "multiply", (
            "invalid tool protocol"
        )
        args = json.loads(call["function"]["arguments"])
        assert set(args) == {"a", "b"} and all(type(v) is int for v in args.values()), "invalid tool args"
        assert sorted(args.values()) == [7, 12], "tool inputs differ from task"
        result = args["a"] * args["b"]
        messages += [
            {"role": "assistant", "content": reply.get("content"), "tool_calls": calls},
            {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"result": result})},
        ]
        final = self.chat(messages, tools=[tool], tool_choice="none", **extra)
        answer = json.loads(final["content"])
        assert answer == {"result": 84} and type(answer["result"]) is int, "tool result not consumed"

    def four_images(self):
        images = [synthetic_image(reverse) for reverse in (False, True, True, False)]
        reply = self.chat(
            self.user(
                "按上传顺序观察四张图。只输出JSON：left_colors是各图左侧方形英文颜色的数组，image_count是图片数量整数。",
                *images,
            ),
            response_format={"type": "json_object"},
        )
        value = json.loads(reply["content"])
        assert value == {"left_colors": ["red", "blue", "blue", "red"], "image_count": 4}, (
            "four-image colors/order incorrect"
        )
        assert type(value["image_count"]) is int, "image_count must be an integer"

    def errors(self):
        cases = [
            {"max_tokens": -1},
            {"temperature": -1},
            {"model": "nonexistent-model"},
            {"messages": self.user("图片数超过部署限制", *([synthetic_image()] * 5))},
            {"messages": self.user("你好 " * 40000), "max_tokens": 1},
        ]
        for override in cases:
            payload = {"model": self.model, "messages": self.user("参数检查"), "max_tokens": 16, **override}
            status, data = self.request("POST", "/chat/completions", payload)
            assert status in {400, 404, 413, 422} and isinstance(data, dict) and data.get("error"), (
                f"invalid request response={status}"
            )

    def timeout_check(self):
        """Exercise the actual socket deadline; a timeout is never a model reply."""
        previous = self.timeout
        self.timeout = 0.001
        try:
            self.request(
                "POST",
                "/chat/completions",
                {
                    "model": self.model,
                    "messages": self.user("请用中文解释图片理解的基本步骤。"),
                    "max_tokens": 32,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
        except TimeoutError:
            return
        except urllib.error.URLError as exc:
            assert isinstance(exc.reason, TimeoutError), "transport failed for a reason other than timeout"
            return
        finally:
            self.timeout = previous
        raise AssertionError("request completed before the forced deadline; timeout path not exercised")

    def long_context(self, path):
        reply = self.chat(self.user(path.read_text(encoding="utf-8")), max_tokens=128)
        usage = self.requests[-1]["response"]["usage"]
        assert 30000 <= usage["prompt_tokens"] <= 32640, "input did not exercise the 32K boundary"
        assert usage["total_tokens"] <= 32768, "context window exceeded"
        assert "容量检查完成" in reply["content"], "long-context instruction not followed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=os.environ.get("HANGZHI_UPSTREAM_MODEL_URL", "http://127.0.0.1:8001/v1")
    )
    parser.add_argument("--model", default=os.environ.get("HANGZHI_UPSTREAM_MODEL_ID", "hangzhi-qwen38"))
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--long-prompt-file", type=Path, help="Optional synthetic prompt of about 31K tokens")
    args = parser.parse_args()
    key = (
        args.key_file.read_text().strip() if args.key_file else os.environ.get("HANGZHI_UPSTREAM_API_KEY", "")
    )
    if not key:
        parser.error("Provide HANGZHI_UPSTREAM_API_KEY or --key-file")
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    probe = Probe(args.base_url, key, args.model, args.timeout)
    deadline = time.monotonic() + args.wait_seconds
    while args.wait_seconds:
        try:
            probe.models()
            break
        except (AssertionError, OSError):
            if time.monotonic() >= deadline:
                break
            time.sleep(2)
    probe.requests.clear()
    probe.check("models", probe.models)
    probe.check("authentication", probe.auth)
    if probe.results[0]["status"] == "passed":
        probe.check("chinese_text", probe.text)
        probe.check("single_image", probe.vision)
        if not args.smoke:
            probe.check("single_image_color_swap", lambda: probe.vision(True))
            probe.check("multiple_images", lambda: probe.visual_json(multi=True))
            probe.check("four_image_limit", probe.four_images)
            probe.check("image_text_json", probe.visual_json)
            probe.check("strict_json_schema", lambda: probe.visual_json(strict=True))
            probe.check("tool_roundtrip", probe.tools)
            probe.check("tools_with_json", lambda: probe.tools(json_mode=True))
            probe.check("invalid_parameters_images_context", probe.errors)
            probe.check("client_socket_timeout", probe.timeout_check)
            if args.long_prompt_file:
                probe.check("near_32k_context", lambda: probe.long_context(args.long_prompt_file))
            for i in range(3):
                probe.check(f"stability_{i + 1}", probe.text)
    report = {
        "base_url": probe.base_url,
        "model": args.model,
        "synthetic_data_only": True,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": probe.results,
        "network_isolation": "not established by this client; requires server-side evidence",
        "restart": "requires independent before/after service restart runs",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if probe.results and all(r["status"] == "passed" for r in probe.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
