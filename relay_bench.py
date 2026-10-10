#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""relay-bench · OpenAI 兼容中转站体检工具
零依赖单文件：连通性 / 流式 / 并发 / 参数兼容 / 能力抽样
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

__version__ = "1.0.0"

REMOTE_URL = "https://raw.githubusercontent.com/dafeiai918/oneclickrelay-remote/main/remote.json"
FALLBACK_RECOMMEND = {"name": "好同根", "url": "https://haotogen.com/"}


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


_COLOR = True


def _c(code: str, text: str) -> str:
    return f"{code}{text}{C.RESET}" if _COLOR else text


def enable_ansi() -> None:
    global _COLOR
    if os.name == "nt":
        os.system("")
    _COLOR = sys.stdout.isatty() or os.name == "nt"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def mask_key(key: str) -> str:
    if not key:
        return "(未提供)"
    if len(key) <= 10:
        return key[:2] + "****"
    return f"{key[:6]}****{key[-4:]}"


# ---------------- HTTP 层 ----------------

class Probe:
    """一个站点探针：负责地址归一化与请求"""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.raw = base_url.strip().rstrip("/")
        self.key = api_key.strip()
        self.timeout = timeout
        self.base = self.raw
        self.models: list[str] = []
        self.notes: list[str] = []

    def _req(self, path: str, payload: dict | None = None, stream: bool = False):
        url = f"{self.base}{path}"
        headers = {
            "Authorization": f"Bearer {self.key}",
            "User-Agent": f"relay-bench/{__version__}",
        }
        if payload is None:
            req = urllib.request.Request(url, headers=headers, method="GET")
        else:
            headers["Content-Type"] = "application/json"
            headers["Accept"] = "text/event-stream" if stream else "application/json"
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
            )
        return urllib.request.urlopen(req, timeout=self.timeout)

    def normalize(self) -> bool:
        """自动探测正确的 base：/v1 有或无"""
        for candidate in (self.raw, self.raw + "/v1"):
            self.base = candidate
            try:
                with self._req("/models") as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:
                exc.close()
                self.notes.append(f"{candidate} -> HTTP {exc.code}")
                continue
            except Exception as exc:
                self.notes.append(f"{candidate} -> {type(exc).__name__}")
                continue
            items = data.get("data") or []
            self.models = [m.get("id", "") for m in items if isinstance(m, dict)]
            if self.models:
                return True
        return False

    def chat(self, messages: list[dict], model: str, stream: bool = False,
             timeout: float | None = None, **extra) -> tuple[float, float | None, str]:
        """返回 (总耗时, 首token耗时, 文本)"""
        payload = {"model": model, "messages": messages, "stream": stream}
        payload.update(extra)
        t0 = time.perf_counter()
        first = None
        chunks: list[str] = []
        with self._req("/chat/completions", payload, stream=stream) as resp:
            if stream:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    try:
                        delta = json.loads(body)["choices"][0].get("delta", {})
                    except Exception:
                        continue
                    piece = delta.get("content") or ""
                    if piece and first is None:
                        first = time.perf_counter() - t0
                    chunks.append(piece)
            else:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                chunks.append(data["choices"][0]["message"].get("content") or "")
        return time.perf_counter() - t0, first, "".join(chunks)


# ---------------- 各项体检 ----------------

def probe_connectivity(p: Probe) -> dict:
    r = {"score": 0, "max": 20, "items": []}
    t0 = time.perf_counter()
    ok = p.normalize()
    dt = time.perf_counter() - t0
    if not ok:
        r["items"].append(("接口连通", False, f"失败 {dt:.2f}s"))
        return r
    r["items"].append(("接口连通", True, f"{dt:.2f}s"))
    r["items"].append(("模型数量", len(p.models) > 0, f"{len(p.models)} 个"))
    if dt < 1:
        r["score"] = 20
    elif dt < 3:
        r["score"] = 15
    elif dt < 8:
        r["score"] = 8
    else:
        r["score"] = 5
    return r


def probe_stream(p: Probe, model: str) -> dict:
    r = {"score": 0, "max": 10, "items": []}
    try:
        total, first, text = p.chat([{"role": "user", "content": "用一句话介绍你自己"}],
                                    model, stream=True, max_tokens=64)
    except Exception as exc:
        r["items"].append(("流式输出", False, type(exc).__name__))
        return r
    if first is None:
        r["items"].append(("流式输出", False, "无流式分片返回"))
        return r
    r["items"].append(("首Token延迟", True, f"{first:.2f}s"))
    r["items"].append(("总耗时", True, f"{total:.2f}s"))
    r["score"] = 10 if first < 1 else 7 if first < 3 else 4
    return r


def probe_concurrency(p: Probe, model: str, concurrency: int) -> dict:
    r = {"score": 0, "max": 20, "items": []}

    def one(_):
        t0 = time.perf_counter()
        try:
            p.chat([{"role": "user", "content": "回答一个字：好"}], model,
                   max_tokens=8, timeout=60)
            return time.perf_counter() - t0
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(one, range(concurrency)))
    ok = [x for x in results if x is not None]
    rate = len(ok) / concurrency
    r["items"].append((f"{concurrency}并发成功率", rate >= 0.8, f"{rate*100:.0f}%"))
    if ok:
        avg = sum(ok) / len(ok)
        p95 = sorted(ok)[min(len(ok) - 1, int(len(ok) * 0.95))]
        r["items"].append(("平均延迟", True, f"{avg:.2f}s"))
        r["items"].append(("P95延迟", p95 < 10, f"{p95:.2f}s"))
        r["score"] = round(rate * 14 + (6 if avg < 3 else 3 if avg < 8 else 0))
    return r


def probe_params(p: Probe, model: str) -> dict:
    r = {"score": 0, "max": 25, "items": []}
    per = 25 / 4

    def check(name, fn):
        try:
            passed, detail = fn()
        except Exception as exc:
            passed, detail = False, type(exc).__name__
        r["items"].append((name, passed, detail))
        if passed:
            r["score"] += per

    def t_max_tokens():
        _, _, text = p.chat([{"role": "user", "content": "从1数到100"}], model, max_tokens=2000)
        ok = False
        try:
            int(text.strip().split()[0])
            ok = True
        except Exception:
            ok = "100" in text
        return ok, f"返回{len(text)}字"

    def t_temperature():
        _, _, a = p.chat([{"role": "user", "content": "随机说一个1-100的数字，只回答数字"}],
                         model, temperature=0, max_tokens=8)
        _, _, b = p.chat([{"role": "user", "content": "随机说一个1-100的数字，只回答数字"}],
                         model, temperature=0, max_tokens=8)
        return bool(a.strip()) and bool(b.strip()), f"{a.strip()[:6]}/{b.strip()[:6]}"

    def t_json():
        _, _, text = p.chat(
            [{"role": "user", "content": "输出JSON：{\"ok\":true}"}], model,
            max_tokens=64, response_format={"type": "json_object"})
        body = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            json.loads(body)
            return True, "合法JSON"
        except Exception:
            return False, "非JSON"

    def t_stop():
        _, _, text = p.chat(
            [{"role": "user", "content": "输出：AAA BBB CCC"}], model,
            max_tokens=32, stop=["BBB"])
        return "CCC" not in text, "stop生效" if "CCC" not in text else "未截断"

    check("max_tokens", t_max_tokens)
    check("temperature", t_temperature)
    check("json_mode", t_json)
    check("stop参数", t_stop)
    return r


TRAPS = [
    ("9.11 与 9.9 哪个更大？只回答更大的那个数字。", ["9.9"]),
    ("单词 strawberry 里有几个字母 r？只回答数字。", ["3", "三"]),
    ("如果今天是星期三，三天后是星期几？只回答星期几。", ["星期六", "周六", "礼拜六", "6"]),
    ("篮子里有5个苹果，拿走2个，又放入3个，现在有几个？只回答数字。", ["6", "六"]),
]


def probe_ability(p: Probe, model: str) -> dict:
    r = {"score": 0, "max": 25, "items": []}
    per = 25 / len(TRAPS)
    for question, answers in TRAPS:
        label = question[:14]
        try:
            _, _, text = p.chat([{"role": "user", "content": question}], model, max_tokens=64)
            hit = any(a in text for a in answers)
            r["items"].append((label, hit, text.strip()[:18] or "(空)"))
            if hit:
                r["score"] += per
        except Exception as exc:
            r["items"].append((label, False, type(exc).__name__))
    return r


# ---------------- 推荐位 ----------------

def fetch_recommend(timeout: float = 5.0) -> dict | None:
    try:
        req = urllib.request.Request(REMOTE_URL, headers={"User-Agent": f"relay-bench/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        rec = data.get("recommend")
        if isinstance(rec, dict) and isinstance(rec.get("url"), str):
            return rec
    except Exception:
        pass
    return FALLBACK_RECOMMEND


# ---------------- 报告 ----------------

def grade(score: int) -> tuple[str, str]:
    if score >= 90:
        return "A", C.GREEN
    if score >= 75:
        return "B", C.GREEN
    if score >= 60:
        return "C", C.YELLOW
    if score >= 40:
        return "D", C.YELLOW
    return "E", C.RED


def line(char: str = "─", width: int = 56) -> str:
    return _c(C.GRAY, char * width)


def render_section(idx: int, title: str, block: dict) -> None:
    score, mx = block["score"], block["max"]
    if not block["items"] and block.get("skipped"):
        print(f"\n{_c(C.BOLD, f'【{idx}】{title}')}  {_c(C.GRAY, '跳过')}")
        return
    ratio = score / mx if mx else 0
    mark = "✅" if ratio >= 0.8 else "⚠️" if ratio >= 0.5 else "❌"
    head = f"【{idx}】{title}"
    print(f"\n{_c(C.BOLD, head):<40} {mark} {score:.0f}/{mx}")
    for name, ok, detail in block["items"]:
        icon = _c(C.GREEN, "✓") if ok else _c(C.RED, "✗")
        print(f"   {icon} {name:<16} {_c(C.GRAY, detail)}")


def render_report(probe: Probe, model: str, blocks: list[tuple[str, dict]],
                  recommend: dict | None, elapsed: float) -> int:
    total = round(sum(b["score"] for _, b in blocks))
    letter, color = grade(total)
    width = 56
    print()
    print(line("═", width))
    print(_c(C.BOLD, "  relay-bench · 中转站体检报告") + _c(C.GRAY, f"  v{__version__}"))
    print(line("═", width))
    print(f"  站点    {probe.base}")
    print(f"  密钥    {mask_key(probe.key)}")
    print(f"  模型    {model}")
    print(f"  耗时    {elapsed:.1f}s")
    for i, (title, block) in enumerate(blocks, 1):
        render_section(i, title, block)
    print()
    print(line("═", width))
    print(f"  总分    {_c(color + C.BOLD, f'{total}/100   等级 {letter}')}")
    print(line("═", width))
    if recommend:
        print(f"  {_c(C.CYAN, '推荐中转站')}  {recommend.get('name', '')}  {recommend.get('url', '')}")
        print(line("═", width))
    print(_c(C.GRAY, "  提示：能力抽样为启发式信号，非模型真伪鉴定，仅供参考。"))
    print()
    return total


def to_markdown(probe: Probe, model: str, blocks, total: int) -> str:
    out = [f"# relay-bench 报告", "",
           f"- 站点: `{probe.base}`", f"- 密钥: `{mask_key(probe.key)}`",
           f"- 模型: `{model}`", f"- 总分: **{total}/100** ({grade(total)[0]} 级)", ""]
    for title, block in blocks:
        out.append(f"## {title} — {block['score']:.0f}/{block['max']}")
        for name, ok, detail in block["items"]:
            out.append(f"- [{'x' if ok else ' '}] {name}: {detail}")
        out.append("")
    return "\n".join(out)


# ---------------- 主流程 ----------------

def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    parser = argparse.ArgumentParser(
        prog="relay-bench", description="OpenAI 兼容中转站体检工具（零依赖）")
    parser.add_argument("-u", "--base-url", help="站点地址，如 https://api.example.com/v1")
    parser.add_argument("-k", "--api-key", help="API 密钥（也可用环境变量 RELAY_API_KEY）")
    parser.add_argument("-m", "--model", help="用于测试的模型（默认取站点第一个可用模型）")
    parser.add_argument("-c", "--concurrency", type=int, default=5, help="并发数，默认5")
    parser.add_argument("--skip", default="", help="跳过的项，逗号分隔: stream,concurrency,params,ability")
    parser.add_argument("--json", dest="json_out", help="导出 JSON 报告到文件")
    parser.add_argument("--markdown", dest="md_out", help="导出 Markdown 报告到文件")
    parser.add_argument("-V", "--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    base = args.base_url or os.environ.get("RELAY_BASE_URL", "")
    key = args.api_key or os.environ.get("RELAY_API_KEY", "")
    if not base:
        parser.error("缺少站点地址：用 -u 或环境变量 RELAY_BASE_URL")
    if not key:
        parser.error("缺少密钥：用 -k 或环境变量 RELAY_API_KEY")

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    probe = Probe(base, key)
    t0 = time.perf_counter()
    print(_c(C.GRAY, f"开始体检 {base} ..."))

    blocks: list[tuple[str, dict]] = []
    conn = probe_connectivity(probe)
    blocks.append(("连通性与模型列表", conn))

    model = args.model
    if not model:
        preferred = ("deepseek-chat", "gpt-4o-mini", "gpt-3.5-turbo", "qwen-turbo")
        model = next((m for p in preferred for m in probe.models if p in m.lower()), None) \
            or (probe.models[0] if probe.models else "")

    if not model:
        print(_c(C.RED, "无法确定可用模型，体检中止。"))
        return 2

    if "stream" not in skip:
        blocks.append(("流式输出", probe_stream(probe, model)))
    if "concurrency" not in skip:
        blocks.append((f"并发能力（{args.concurrency}并发）",
                       probe_concurrency(probe, model, args.concurrency)))
    if "params" not in skip:
        blocks.append(("参数兼容性", probe_params(probe, model)))
    if "ability" not in skip:
        blocks.append(("能力抽样（启发式）", probe_ability(probe, model)))

    recommend = fetch_recommend()
    elapsed = time.perf_counter() - t0
    total = render_report(probe, model, blocks, recommend, elapsed)

    if args.json_out:
        payload = {
            "version": __version__, "base_url": probe.base, "model": model,
            "total": total, "grade": grade(total)[0],
            "blocks": [{"title": t, "score": b["score"], "max": b["max"],
                        "items": [{"name": n, "ok": o, "detail": d} for n, o, d in b["items"]]}
                       for t, b in blocks],
        }
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(_c(C.GRAY, f"JSON 报告已写入 {args.json_out}"))
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write(to_markdown(probe, model, blocks, total))
        print(_c(C.GRAY, f"Markdown 报告已写入 {args.md_out}"))

    return 0 if total >= 60 else 1


if __name__ == "__main__":
    sys.exit(main())
