from __future__ import annotations

import re
from dataclasses import dataclass


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def marker_pattern(k: int) -> re.Pattern[str]:
    return re.compile(r"(?<!\d)" + re.escape(str(k)) + r"[\)）]")


def find_next_marker(text: str, k: int, start: int) -> re.Match[str] | None:
    return marker_pattern(k).search(text, start)


def body_span_after_match(text: str, m: re.Match[str]) -> tuple[int, int]:
    a = m.end()
    nl = text.find("\n", a)
    if nl < 0:
        b = len(text)
    else:
        b = nl
    return (a, b)


def advance_search_after_body(text: str, body_end_exclusive: int) -> int:
    if body_end_exclusive < len(text) and text[body_end_exclusive] == "\n":
        return body_end_exclusive + 1
    return body_end_exclusive


def removal_span_after_ok(text: str, m: re.Match[str], body_end_exclusive: int) -> tuple[int, int]:
    lo = m.start()
    hi = advance_search_after_body(text, body_end_exclusive)
    return (lo, hi)


def merge_intervals(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted((lo, hi) for lo, hi in spans if lo < hi)
    if not ordered:
        return []
    out: list[tuple[int, int]] = []
    cur_lo, cur_hi = ordered[0]
    for lo, hi in ordered[1:]:
        if lo <= cur_hi:
            cur_hi = max(cur_hi, hi)
        else:
            out.append((cur_lo, cur_hi))
            cur_lo, cur_hi = lo, hi
    out.append((cur_lo, cur_hi))
    return out


def build_body_without_spans(full_text: str, spans: list[tuple[int, int]]) -> str:
    merged = merge_intervals(spans)
    if not merged:
        return full_text
    parts: list[str] = []
    cur = 0
    n = len(full_text)
    for lo, hi in merged:
        if lo > n:
            break
        hi = min(hi, n)
        if cur < lo:
            parts.append(full_text[cur:lo])
        cur = max(cur, hi)
    parts.append(full_text[cur:])
    return "".join(parts)


def collect_sequential_marker_spans(
    text: str, n: int
) -> tuple[list[tuple[int, int, int, int]], int | None]:
    spans: list[tuple[int, int, int, int]] = []
    search_from = 0
    for k in range(1, n + 1):
        m = find_next_marker(text, k, search_from)
        if m is None:
            return spans, k
        lo, me = m.start(), m.end()
        spans.append((k, lo, me, me))
        search_from = me
    return spans, None


def apply_insert_replacements(
    text: str, spans: list[tuple[int, int, int, int]], contents: list[str]
) -> str:
    out = text
    for k, lo, hi, _me in sorted(spans, key=lambda t: t[1], reverse=True):
        body = contents[k - 1].strip()
        repl = f"(주석 {k} 시작){body}(주석 {k} 끝)"
        out = out[:lo] + repl + out[hi:]
    return out


def parse_bulk_comment_blocks(raw: str) -> tuple[dict[int, str], list[str], list[str]]:
    text = normalize_newlines(raw).strip()
    if not text:
        return {}, [], []

    out_map: dict[int, str] = {}
    errors: list[str] = []
    warnings: list[str] = []
    blocks = re.split(r"\n\s*\n+", text)
    for bi, block in enumerate(blocks, start=1):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        m = re.match(r"(?<!\d)(\d+)[\)）]\s*(.*)$", lines[0])
        if not m:
            errors.append(f"블록 {bi}: 첫 줄이 「숫자)」형식이 아닙니다 ({lines[0][:48]!r}…).")
            continue
        k = int(m.group(1))
        rest = [m.group(2)] + lines[1:]
        body = "\n".join(rest).strip()
        if k in out_map:
            warnings.append(f"번호 {k}가 중복입니다. 뒤 블록을 사용합니다.")
        out_map[k] = body

    return out_map, errors, warnings


@dataclass(frozen=True)
class AnchorMatch:
    index: int
    anchor: str


def split_anchor_lines(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [ln.strip() for ln in lines if ln.strip()]


def find_all_occurrences(haystack: str, needle: str) -> list[int]:
    if not needle:
        return []
    out: list[int] = []
    pos = 0
    while True:
        i = haystack.find(needle, pos)
        if i < 0:
            break
        out.append(i)
        pos = i + 1
    return out


def collect_matches(target: str, anchor_lines: list[str]) -> tuple[list[AnchorMatch], list[str]]:
    raw: list[AnchorMatch] = []
    not_found: list[str] = []
    for a in anchor_lines:
        hits = find_all_occurrences(target, a)
        if not hits:
            not_found.append(a)
            continue
        for idx in hits:
            raw.append(AnchorMatch(index=idx, anchor=a))

    raw.sort(key=lambda m: m.index)
    deduped: list[AnchorMatch] = []
    seen: set[int] = set()
    for m in raw:
        if m.index in seen:
            continue
        seen.add(m.index)
        deduped.append(m)
    return deduped, not_found


def build_result_with_breaks(source: str, break_indices: list[int]) -> str:
    uniq = sorted({i for i in break_indices if 0 <= i <= len(source)})
    out = source
    for idx in reversed(uniq):
        if idx > 0 and out[idx - 1] == "\n":
            continue
        out = out[:idx] + "\n" + out[idx:]
    return out
