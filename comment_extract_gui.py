from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

import streamlit as st

from comment_extract_gui import (
    AnchorMatch,
    advance_search_after_body,
    apply_insert_replacements,
    body_span_after_match,
    build_body_without_spans,
    build_result_with_breaks,
    collect_matches,
    collect_sequential_marker_spans,
    find_next_marker,
    normalize_newlines,
    parse_bulk_comment_blocks,
    removal_span_after_ok,
    split_anchor_lines,
)


@dataclass
class ExtractState:
    full_text: str
    max_n: int
    current_k: int = 1
    search_from: int = 0
    results: list[tuple[int, str]] = field(default_factory=list)
    removed_spans: list[tuple[int, int]] = field(default_factory=list)
    candidate_span: tuple[int, int] | None = None
    done: bool = False


@dataclass
class InsertState:
    full_text: str
    n: int
    contents: list[str]
    current_k: int = 1
    search_from: int = 0
    confirmed_spans: list[tuple[int, int, int, int]] = field(default_factory=list)
    candidate_span: tuple[int, int] | None = None
    done: bool = False


@dataclass
class LinebreakState:
    target_raw: str
    pending: list[AnchorMatch]
    queue_pos: int = 0
    approved_break_indices: list[int] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)


def _refresh_extract_candidate(s: ExtractState) -> None:
    if s.done:
        s.candidate_span = None
        return
    m = find_next_marker(s.full_text, s.current_k, s.search_from)
    s.candidate_span = None if m is None else m.span()


def _extract_candidate_preview(s: ExtractState) -> tuple[str, str]:
    if s.candidate_span is None:
        return "", "후보가 없습니다."
    ms, me = s.candidate_span
    m = find_next_marker(s.full_text, s.current_k, ms)
    if m is None:
        return "", "후보가 없습니다."
    a, b = body_span_after_match(s.full_text, m)
    body = s.full_text[a:b]
    status = f"{s.current_k}/{s.max_n} · marker {m.group()!r} · 본문 길이 {len(body.strip())}"
    return body, status


def render_extract_tab() -> None:
    st.subheader("주석 추출")
    n = st.number_input("마지막 주석 번호 N", min_value=1, value=10, step=1, key="extract_n")
    source = st.text_area("입력 텍스트", height=220, key="extract_source")

    c1, c2, c3, c4 = st.columns(4)
    if c1.button("시작", use_container_width=True, key="extract_start_btn"):
        text = normalize_newlines(source).rstrip("\n")
        if not text.strip():
            st.warning("텍스트가 비어 있습니다.")
        else:
            st.session_state.extract_state = ExtractState(full_text=text, max_n=int(n))
            st.session_state.extract_undo = []
            _refresh_extract_candidate(st.session_state.extract_state)
            st.success("추출 세션을 시작했습니다.")

    if c2.button("OK 확정", use_container_width=True, key="extract_ok_btn"):
        s: ExtractState | None = st.session_state.get("extract_state")
        if s is None or s.done:
            st.warning("진행 중인 세션이 없습니다.")
        elif s.candidate_span is None:
            st.warning("확정할 후보가 없습니다.")
        else:
            ms, _me = s.candidate_span
            m = find_next_marker(s.full_text, s.current_k, ms)
            if m is None:
                st.warning("후보를 찾지 못했습니다. 다음 후보를 눌러 주세요.")
            else:
                a, b = body_span_after_match(s.full_text, m)
                st.session_state.extract_undo.append(
                    (deepcopy(s.results), s.current_k, s.search_from, deepcopy(s.removed_spans))
                )
                s.results.append((s.current_k, s.full_text[a:b].strip()))
                s.removed_spans.append(removal_span_after_ok(s.full_text, m, b))
                next_from = advance_search_after_body(s.full_text, b)
                if s.current_k >= s.max_n:
                    s.done = True
                    s.search_from = next_from
                    s.candidate_span = None
                    st.success("모든 주석 추출을 완료했습니다.")
                else:
                    s.current_k += 1
                    s.search_from = next_from
                    _refresh_extract_candidate(s)

    if c3.button("다음 후보", use_container_width=True, key="extract_next_btn"):
        s: ExtractState | None = st.session_state.get("extract_state")
        if s is None or s.done or s.candidate_span is None:
            st.warning("다음으로 이동할 후보가 없습니다.")
        else:
            s.search_from = s.candidate_span[0] + 1
            _refresh_extract_candidate(s)

    if c4.button("되돌리기", use_container_width=True, key="extract_undo_btn"):
        s: ExtractState | None = st.session_state.get("extract_state")
        stack = st.session_state.get("extract_undo", [])
        if s is None or not stack:
            st.warning("되돌릴 단계가 없습니다.")
        else:
            prev_results, prev_k, prev_from, prev_spans = stack.pop()
            s.results = prev_results
            s.current_k = prev_k
            s.search_from = prev_from
            s.removed_spans = prev_spans
            s.done = False
            _refresh_extract_candidate(s)

    s: ExtractState | None = st.session_state.get("extract_state")
    if s is None:
        return

    preview, status = _extract_candidate_preview(s)
    st.caption(status)
    st.text_area("현재 후보 본문", value=preview, height=120, disabled=True)

    result = "\n\n".join(f"{num}) {body}".strip() for num, body in s.results)
    body_only = build_body_without_spans(s.full_text, s.removed_spans) if s.removed_spans else s.full_text
    st.text_area("추출 결과", value=result, height=180, key="extract_result_area")
    st.text_area("본문 (주석 제거 후)", value=body_only, height=180, key="extract_body_area")


def _gather_insert_contents(raw_bulk: str, n: int) -> tuple[list[str] | None, list[str], list[str]]:
    parsed, errors, warnings = parse_bulk_comment_blocks(raw_bulk)
    if errors:
        return None, errors, warnings
    missing = [k for k in range(1, n + 1) if k not in parsed]
    if missing:
        return None, [f"다음 번호 블록이 없습니다: {', '.join(str(x) for x in missing)}"], warnings
    return [parsed[k] for k in range(1, n + 1)], [], warnings


def _refresh_insert_candidate(s: InsertState) -> None:
    if s.done:
        s.candidate_span = None
        return
    m = find_next_marker(s.full_text, s.current_k, s.search_from)
    s.candidate_span = None if m is None else m.span()


def render_insert_tab() -> None:
    st.subheader("주석 삽입")
    n = st.number_input("주석 개수 N", min_value=1, value=3, step=1, key="insert_n")
    body = st.text_area("본문 (1) 2) ... 표식 포함)", height=180, key="insert_body")
    bulk = st.text_area("주석 내용(블록 사이 빈 줄)", height=180, key="insert_bulk")

    a, b, c, d, e = st.columns(5)
    if a.button("단계 확인 시작", use_container_width=True, key="insert_step_start_btn"):
        text = normalize_newlines(body).rstrip("\n")
        contents, errors, warns = _gather_insert_contents(bulk, int(n))
        if not text.strip():
            st.warning("본문이 비어 있습니다.")
        elif contents is None:
            for msg in errors:
                st.error(msg)
        else:
            st.session_state.insert_state = InsertState(full_text=text, n=int(n), contents=contents)
            st.session_state.insert_undo = []
            _refresh_insert_candidate(st.session_state.insert_state)
            for w in warns:
                st.warning(w)
            st.success("단계 확인 세션을 시작했습니다.")

    if b.button("OK 위치 확정", use_container_width=True, key="insert_ok_btn"):
        s: InsertState | None = st.session_state.get("insert_state")
        if s is None or s.done:
            st.warning("진행 중인 세션이 없습니다.")
        elif s.candidate_span is None:
            st.warning("확정할 후보가 없습니다.")
        else:
            ms, _me = s.candidate_span
            m = find_next_marker(s.full_text, s.current_k, ms)
            if m is None:
                st.warning("후보를 찾지 못했습니다.")
            else:
                st.session_state.insert_undo.append(
                    (deepcopy(s.confirmed_spans), s.current_k, s.search_from)
                )
                me = m.end()
                s.confirmed_spans.append((s.current_k, m.start(), me, me))
                s.search_from = me
                if s.current_k >= s.n:
                    s.done = True
                    s.candidate_span = None
                    st.success("단계 확인 완료.")
                else:
                    s.current_k += 1
                    _refresh_insert_candidate(s)

    if c.button("다음 후보", use_container_width=True, key="insert_next_btn"):
        s: InsertState | None = st.session_state.get("insert_state")
        if s is None or s.done or s.candidate_span is None:
            st.warning("다음으로 이동할 후보가 없습니다.")
        else:
            s.search_from = s.candidate_span[0] + 1
            _refresh_insert_candidate(s)

    if d.button("되돌리기", use_container_width=True, key="insert_undo_btn"):
        s: InsertState | None = st.session_state.get("insert_state")
        stack = st.session_state.get("insert_undo", [])
        if s is None or not stack:
            st.warning("되돌릴 단계가 없습니다.")
        else:
            spans, prev_k, prev_from = stack.pop()
            s.confirmed_spans = spans
            s.current_k = prev_k
            s.search_from = prev_from
            s.done = False
            _refresh_insert_candidate(s)

    if e.button("미리보기/결과 생성", use_container_width=True, key="insert_preview_build_btn"):
        text = normalize_newlines(body).rstrip("\n")
        contents, errors, warns = _gather_insert_contents(bulk, int(n))
        if not text.strip():
            st.warning("본문이 비어 있습니다.")
        elif contents is None:
            for msg in errors:
                st.error(msg)
        else:
            spans, missing_k = collect_sequential_marker_spans(text, int(n))
            if missing_k is not None:
                st.error(f"{missing_k}번 표식을 찾지 못했습니다.")
            else:
                st.session_state.insert_preview_result = apply_insert_replacements(text, spans, contents)
                for w in warns:
                    st.warning(w)
                st.success("결과를 생성했습니다.")

    s: InsertState | None = st.session_state.get("insert_state")
    if s is not None:
        if s.candidate_span is None and not s.done:
            st.caption(f"{s.current_k}/{s.n} · 후보 없음")
        elif not s.done and s.candidate_span is not None:
            ms, _ = s.candidate_span
            m = find_next_marker(s.full_text, s.current_k, ms)
            marker = m.group() if m else "?"
            st.caption(f"{s.current_k}/{s.n} · 현재 표식 {marker!r}")
        elif s.done:
            st.caption("단계 확인 완료")
        step_result = apply_insert_replacements(s.full_text, s.confirmed_spans, s.contents) if s.done else ""
        st.text_area("단계 확인 결과", value=step_result, height=160, key="insert_step_result")

    st.text_area(
        "일괄 결과",
        value=st.session_state.get("insert_preview_result", ""),
        height=220,
        key="insert_preview_area",
    )


def _current_linebreak_match(s: LinebreakState) -> AnchorMatch | None:
    if s.queue_pos >= len(s.pending):
        return None
    return s.pending[s.queue_pos]


def render_linebreak_tab() -> None:
    st.subheader("수동 줄바꿈")
    target = st.text_area("대상 텍스트", height=220, key="lb_target")
    anchors_raw = st.text_area("앵커 목록(한 줄에 하나)", height=220, key="lb_anchors")

    c1, c2, c3, c4, c5 = st.columns(5)
    if c1.button("세션 시작", use_container_width=True, key="lb_start_btn"):
        target_raw = normalize_newlines(target).rstrip("\n")
        lines = split_anchor_lines(anchors_raw)
        if not target_raw.strip():
            st.warning("대상 텍스트가 비어 있습니다.")
        elif not lines:
            st.warning("앵커가 비어 있습니다.")
        else:
            pending, not_found = collect_matches(target_raw, lines)
            st.session_state.lb_state = LinebreakState(
                target_raw=target_raw,
                pending=pending,
                not_found=not_found,
            )
            st.session_state.lb_undo = []
            if not_found:
                st.warning(f"미매칭 앵커 {len(not_found)}개")
            st.success("세션 시작 완료")

    if c2.button("여기서 줄바꿈", use_container_width=True, key="lb_approve_btn"):
        s: LinebreakState | None = st.session_state.get("lb_state")
        if s is None:
            st.warning("세션이 없습니다.")
        else:
            cur = _current_linebreak_match(s)
            if cur is None:
                st.warning("처리할 후보가 없습니다.")
            else:
                st.session_state.lb_undo.append((s.queue_pos, tuple(s.approved_break_indices)))
                s.approved_break_indices.append(cur.index)
                s.approved_break_indices.sort()
                s.queue_pos += 1

    if c3.button("건너뛰기", use_container_width=True, key="lb_skip_btn"):
        s: LinebreakState | None = st.session_state.get("lb_state")
        if s is None:
            st.warning("세션이 없습니다.")
        else:
            cur = _current_linebreak_match(s)
            if cur is None:
                st.warning("처리할 후보가 없습니다.")
            else:
                st.session_state.lb_undo.append((s.queue_pos, tuple(s.approved_break_indices)))
                s.queue_pos += 1

    if c4.button("되돌리기", use_container_width=True, key="lb_undo_btn"):
        s: LinebreakState | None = st.session_state.get("lb_state")
        stack = st.session_state.get("lb_undo", [])
        if s is None or not stack:
            st.warning("되돌릴 단계가 없습니다.")
        else:
            qpos, approved = stack.pop()
            s.queue_pos = qpos
            s.approved_break_indices = list(approved)

    if c5.button("앵커 전체 적용", use_container_width=True, key="lb_apply_all_btn"):
        target_raw = normalize_newlines(target).rstrip("\n")
        lines = split_anchor_lines(anchors_raw)
        if not target_raw.strip():
            st.warning("대상 텍스트가 비어 있습니다.")
        elif not lines:
            st.warning("앵커가 비어 있습니다.")
        else:
            pending, not_found = collect_matches(target_raw, lines)
            all_indices = sorted({m.index for m in pending})
            st.session_state.lb_state = LinebreakState(
                target_raw=target_raw,
                pending=pending,
                queue_pos=len(pending),
                approved_break_indices=all_indices,
                not_found=not_found,
            )
            st.session_state.lb_undo = []
            st.success(f"{len(all_indices)}곳 적용 완료")
            if not_found:
                st.warning(f"미매칭 앵커 {len(not_found)}개")

    s: LinebreakState | None = st.session_state.get("lb_state")
    if s is None:
        return

    cur = _current_linebreak_match(s)
    if cur is None:
        st.caption("모든 후보 처리 완료")
    else:
        st.caption(f"[{s.queue_pos + 1}/{len(s.pending)}] 앵커: {cur.anchor!r} · 인덱스: {cur.index}")
        lo = max(0, cur.index - 48)
        hi = min(len(s.target_raw), cur.index + len(cur.anchor) + 48)
        seg = s.target_raw[lo:hi]
        pipe_at = cur.index - lo
        preview = seg[:pipe_at] + "|" + seg[pipe_at:]
        st.text_area("현재 후보 미리보기", value=preview, height=120, disabled=True)

    out = build_result_with_breaks(s.target_raw, s.approved_break_indices)
    st.text_area("결과 텍스트", value=out, height=260, key="lb_result")
    st.download_button(
        "결과 다운로드(txt)",
        out,
        file_name="linebreak_result.txt",
        mime="text/plain",
        key="lb_download_btn",
    )


def main() -> None:
    st.set_page_config(page_title="주석/줄바꿈 웹 도구", layout="wide")
    st.title("주석(번호) 도구 · Web")
    tab1, tab2, tab3 = st.tabs(["주석 추출", "주석 삽입", "수동 줄바꿈"])
    with tab1:
        render_extract_tab()
    with tab2:
        render_insert_tab()
    with tab3:
        render_linebreak_tab()


if __name__ == "__main__":
    main()
