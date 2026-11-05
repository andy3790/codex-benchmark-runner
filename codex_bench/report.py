from __future__ import annotations

import csv
import html
import io
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

from .pricing import API_COST_FIELD, API_COST_LABEL, API_PRICING, estimate_api_cost_usd, format_api_cost
from .util import BenchError, read_json, replace_file, utc_now


def read_saved_results(output: Path) -> tuple[dict, list[dict]]:
    """Read the original experiment matrix without needing its source inputs."""
    metadata = read_json(output / "manifest.json")
    if not isinstance(metadata, dict) or type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1:
        raise BenchError("Saved manifest must use schema_version=1")
    jobs = metadata.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise BenchError("Saved manifest must contain a nonempty jobs list")
    rows, seen = [], set()
    statuses = {"pending", "running", "grading", "succeeded", "failed", "unsupported", "timed_out", "interrupted"}
    for job in jobs:
        if (not isinstance(job, dict)
                or not isinstance(job.get("id"), str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", job["id"])
                or any(not isinstance(job.get(key), str) or not job[key].strip() for key in ("model", "effort"))
                or type(job.get("repeat")) is not int or job["repeat"] < 1):
            raise BenchError("Saved manifest contains an invalid job")
        if job["id"] in seen:
            raise BenchError(f"Saved manifest contains a duplicate job: {job['id']}")
        seen.add(job["id"])
        path = output / "runs" / job["id"] / "result.json"
        if not path.resolve().is_relative_to(output.resolve()):
            raise BenchError(f"Saved result path leaves the experiment: {job['id']}")
        row = read_json(path) if path.exists() else {**job, "status": "pending", "usage": {}}
        if (not isinstance(row, dict)
                or row.get("id") != job["id"]
                or any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("model", "effort"))
                or type(row.get("repeat")) is not int or row["repeat"] < 1
                or not isinstance(row.get("status"), str) or row["status"] not in statuses
                or any(row.get(key) is not None and not isinstance(row[key], dict) for key in ("usage", "grade"))):
            raise BenchError(f"Saved result does not match its manifest job: {job['id']}")
        rows.append(row)
    return metadata, rows


def ratio(numerator, denominator, scale=1):
    return numerator / denominator * scale if numerator is not None and denominator is not None and denominator > 0 else None


def flatten(row: dict) -> dict:
    usage = row.get("usage") or {}
    grade = row.get("grade") or {}
    result = {k: row.get(k) for k in ["id", "model", "effort", "repeat", "status", "success", "started_at", "ended_at",
                                                   "codex_version", "exit_code", "generation_seconds", "grading_seconds", "duration_seconds", "error_type", "grading_error"]}
    result.update({k: usage.get(k) for k in ["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"]})
    result.update({k: grade.get(k) for k in ["score", "max_score", "planned_max_score", "pass_rate", "measured_tests", "skipped_tests"]})
    result[API_COST_FIELD] = row[API_COST_FIELD] if API_COST_FIELD in row else estimate_api_cost_usd(row)
    result["cache_ratio"] = ratio(usage.get("cached_input_tokens"), usage.get("input_tokens"))
    result["reasoning_ratio"] = ratio(usage.get("reasoning_output_tokens"), usage.get("output_tokens"))
    result["score_per_1000_tokens"] = ratio(grade.get("score"), usage.get("total_tokens"), 1000)
    result["score_per_second"] = ratio(grade.get("score"), row.get("generation_seconds"))
    result["usage_source"] = row.get("usage_source")
    result["artifact_path"] = row.get("artifact_path")
    return result


METRICS = ["score", "max_score", "pass_rate", "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
           "total_tokens", "cache_ratio", "reasoning_ratio", "generation_seconds", "grading_seconds", "duration_seconds",
           "score_per_1000_tokens", "score_per_second", API_COST_FIELD]


def describe(values: list) -> dict:
    known = [v for v in values if v is not None]
    return {"n": len(known), "missing": len(values) - len(known),
            "mean": statistics.mean(known) if known else None,
            "min": min(known) if known else None, "max": max(known) if known else None,
            "stddev": statistics.pstdev(known) if known else None}


def summarize(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["model"], row["effort"])].append(row)
    result = []
    for (model, effort), items in sorted(grouped.items()):
        flattened = [flatten(r) for r in items]
        categories = defaultdict(list)
        for row in items:
            by_category = defaultdict(list)
            for test in (row.get("grade") or {}).get("tests", []):
                by_category[test["category"]].append(test)
            for category, tests in by_category.items():
                scores = [t["score"] for t in tests if t["score"] is not None]
                categories[category].append(sum(scores) if scores else None)
        category_stats = {c: describe(v + [None] * (len(items) - len(v))) for c, v in categories.items()}
        result.append({"model": model, "effort": effort, "runs": len(items),
                       "statuses": dict(Counter(r["status"] for r in items)),
                       "error_types": dict(Counter(r.get("error_type") or r.get("grading_error") for r in items if r.get("error_type") or r.get("grading_error"))),
                       "metrics": {m: describe([r.get(m) for r in flattened]) for m in METRICS}, "categories": category_stats})
    return result


def summarize_experiment_cost(rows: list[dict], attempts: dict[Path, dict]) -> dict:
    # History paths identify each stored generation, including legacy records without an ID field.
    costs = {(path.parents[2].name, path.parent.name): row[API_COST_FIELD] for path, row in attempts.items()}
    jobs_with_history = {job_id for job_id, _ in costs}
    for row in rows:
        if row["status"] == "pending":
            continue
        job_id = row["id"]
        attempt_id = row.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            path = row.get("artifact_path")
            parts = path.replace("\\", "/").rstrip("/").split("/") if isinstance(path, str) else []
            attempt_id = parts[3] if len(parts) == 4 and parts[:3] == ["runs", job_id, "attempts"] else None
        if not attempt_id and job_id in jobs_with_history:
            # An unidentified legacy current row can already be represented by a history record.
            continue
        key = (job_id, attempt_id)
        cost = row[API_COST_FIELD]
        if cost is not None or key not in costs:
            costs[key] = cost

    known = [cost for cost in costs.values() if cost is not None]
    return {"total_estimated_api_cost_usd": round(math.fsum(known), 8) if known else None,
            "priced_attempts": len(known), "unpriced_attempts": len(costs) - len(known)}


def fmt(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.3f}"
    return str(value)


def esc(value):
    return html.escape(fmt(value), quote=True)


def bars(groups: list[dict], metric: str, title: str, color: str) -> str:
    values = [g["metrics"][metric]["mean"] for g in groups]
    maximum = max((v for v in values if v is not None), default=1) or 1
    parts = [f'<div class="chart"><h3>{esc(title)}</h3>']
    for group, value in zip(groups, values):
        percent = value / maximum * 100 if value is not None else 0
        parts.append(f'<div class="barrow"><span>{esc(group["model"])} · {esc(group["effort"])}</span><div class="track"><i style="width:{percent:.3f}%;background:{color}"></i></div><b>{esc(value)}</b></div>')
    return "".join(parts) + "</div>"


STYLE = """
:root{font-family:Segoe UI,system-ui,sans-serif;color:#dfe7f4;background:#101723}*{box-sizing:border-box}body{margin:0;padding:40px;max-width:1800px;margin:auto}h1{font-size:32px;margin:0 0 8px}h2{margin:32px 0 12px}h3{margin-top:0}p{color:#b9c6da;line-height:1.6}.badge{padding:4px 10px;background:#25394f;border-radius:12px;color:#8dd9ff}.cards,.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin:24px 0}.card,.chart,details{padding:20px;background:#192436;border:1px solid #2a3a52;border-radius:12px}.card strong{display:block;font-size:28px;margin-top:8px}.chart{min-width:0}.barrow{display:grid;grid-template-columns:minmax(110px,1fr) 2fr 70px;gap:12px;align-items:center;margin:12px 0;font-size:12px}.barrow span{overflow-wrap:anywhere}.barrow b{text-align:right}.track{height:13px;background:#0e1623;border-radius:4px}.track i{display:block;height:100%;border-radius:4px}table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}th,td{text-align:left;padding:12px;border-bottom:1px solid #2a3a52}th{background:#23324a;cursor:pointer}th button{all:unset;cursor:pointer}tbody tr:hover{background:#22334b}.scroll{overflow:auto;border:1px solid #2a3a52;border-radius:8px}a{color:#8dd9ff}details{margin:12px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;color:#bed1e9}input{padding:10px;background:#192436;color:#dfe7f4;border:1px solid #45617d;border-radius:6px;width:300px;margin:0 0 12px}.failed,.unsupported,.timed_out{color:#ffa6a6}.succeeded{color:#8ee1bf}.pending,.interrupted{color:#ffd789}.muted{color:#96aac5}caption{text-align:left;padding:8px}footer{margin-top:28px;color:#96aac5}@media(max-width:600px){body{padding:16px}}
"""
SCRIPT = """
document.querySelectorAll('table.sortable').forEach(table=>table.querySelectorAll('th').forEach((th,index)=>{
th.querySelector('button').addEventListener('click',()=>{const direction=th.dataset.direction==='asc'?'desc':'asc';th.dataset.direction=direction;
const rows=Array.from(table.tBodies[0].rows);rows.sort((a,b)=>{let x=a.cells[index].dataset.value,y=b.cells[index].dataset.value;if(x==='')return y===''?0:1;if(y==='')return -1;const nx=Number(x),ny=Number(y);const cmp=x.trim()!==''&&y.trim()!==''&&Number.isFinite(nx)&&Number.isFinite(ny)?nx-ny:x.localeCompare(y);return direction==='asc'?cmp:-cmp});rows.forEach(row=>table.tBodies[0].appendChild(row));});}));
document.getElementById('filter').addEventListener('input',e=>{document.querySelectorAll('#runs tbody tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(e.target.value.toLowerCase()));});
"""


def table(headers, rows, table_id="", *, cost_column=None):
    head = "".join(f'<th scope="col"><button title="Sort">{esc(x)}</button></th>' for x in headers)

    def cell(value, index):
        key = html.escape(str(value) if value is not None else "", quote=True)
        label = format_api_cost(value) if index == cost_column else fmt(value)
        return f'<td data-value="{key}">{html.escape(label, quote=True)}</td>'

    body = "".join("<tr>" + "".join(cell(value, index) for index, value in enumerate(row)) + "</tr>" for row in rows)
    return f'<div class="scroll"><table class="sortable" id="{table_id}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def artifact_links(path: str, filenames: list[str]) -> str:
    return "".join(f'<a href="{quote(path + "/" + filename, safe="/")}">{esc(filename)}</a> &nbsp; '
                   for filename in filenames)


def render_html(rows: list[dict], groups: list[dict], metadata: dict, experiment_cost: dict) -> str:
    title = "Codex Benchmark Report"
    failures = sum(r["status"] in {"failed", "unsupported", "timed_out"} for r in rows)
    scored = sum((r.get("grade") or {}).get("score") is not None for r in rows)
    total_cost = format_api_cost(experiment_cost["total_estimated_api_cost_usd"])
    cost_card = (f'<div class="card">총 예상 API 비용 (USD, 재시도 포함)<strong>{total_cost}</strong>'
                 f'<span class="muted">비용 산정 {experiment_cost["priced_attempts"]}회 · '
                 f'미산정 {experiment_cost["unpriced_attempts"]}회</span></div>')
    parts = [f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{STYLE}</style></head><body><span class="badge">OFFLINE · INDEPENDENT GRADING</span><h1>{title}</h1>',
             f'<p>실험 {esc(metadata.get("experiment_id"))} · 생성 {esc(utc_now())} · 표준편차: 모집단 기준</p>',
             f'<div class="cards"><div class="card">전체 실험<strong>{len(rows)}</strong></div><div class="card">실패 / 미지원 / 시간 초과<strong>{failures}</strong></div><div class="card">실제 점수 측정<strong>{scored} / {len(rows)}</strong></div>{cost_card}</div>',
             '<p>— = 측정 불가. 누락값은 0으로 대체하지 않습니다. 통계의 n은 측정된 실행 수입니다. 실행 성공 여부와 기능 테스트 통과는 별도입니다. 점수/시간 효율은 생성 시간 기준이며 채점 시간을 제외합니다. 총 토큰 = 입력 + 출력; 캐시/추론은 하위 항목입니다.</p>',
             '<div class="charts">', bars(groups, "score", "모델 · 추론 강도별 평균 점수", "#79d9ba"),
             bars(groups, "total_tokens", "평균 총 토큰", "#7db8ff"), bars(groups, "generation_seconds", "평균 생성 시간 (초)", "#d9acff"), "</div>",
             '<h2>모델 · 추론 강도별 평균 예상 API 비용</h2>',
             '<p>각 실험의 현재 시도에서 비용을 산정할 수 있는 값으로 평균을 계산합니다. '
             '총액은 산정 가능한 모든 생성 시도와 재시도를 합산합니다.</p>']
    cost_rows = []
    for group in groups:
        stats = group["metrics"][API_COST_FIELD]
        cost_rows.append([group["model"], group["effort"], stats["n"], stats["missing"], stats["mean"]])
    parts.append(table(["모델", "추론 강도", "산정", "미산정", "평균 예상 API 비용 (USD)"],
                       cost_rows, "api-costs", cost_column=4))
    parts.append('<h2>반복 실행 통계</h2><p>모든 조합을 포함합니다. 실패 실행에 점수가 없으면 missing에 포함하며 평균은 측정값으로만 계산합니다.</p>')
    stat_rows = []
    for group in groups:
        for metric, stats in group["metrics"].items():
            if metric == API_COST_FIELD:
                continue
            stat_rows.append([group["model"], group["effort"], metric, group["runs"], stats["n"], stats["missing"], stats["mean"], stats["min"], stats["max"], stats["stddev"]])
    parts.append(table(["모델", "추론", "지표", "전체", "n", "missing", "평균", "최소", "최대", "표준편차"], stat_rows))
    category_rows = [[g["model"], g["effort"], category, stats["n"], stats["missing"], stats["mean"], stats["min"], stats["max"], stats["stddev"]]
                     for g in groups for category, stats in g["categories"].items()]
    if category_rows:
        parts.append('<h2>범주별 점수</h2>')
        parts.append(table(["모델", "추론", "범주", "n", "missing", "평균", "최소", "최대", "표준편차"], category_rows))
    error_rows = [[g["model"], g["effort"], kind, count] for g in groups for kind, count in g["error_types"].items()]
    if error_rows:
        parts.append('<h2>오류 유형 비교</h2>')
        parts.append(table(["모델", "추론", "오류", "횟수"], error_rows))
    parts.extend(['<h2>전체 실행</h2><input id="filter" aria-label="결과 필터" placeholder="모델, 추론 강도, 오류 검색">'])
    parts.append('<p>예상 API 비용은 Standard 기본 텍스트 단가로 환산합니다. '
                 '장문 요청 할증, 캐시 쓰기 요금, 별도 도구 요금은 계산 범위에서 제외합니다. '
                 '단가나 토큰을 확인할 수 없거나 모의 실행이면 —로 표시합니다. '
                 f'<a href="{API_PRICING["source"]}">OpenAI 공식 API 가격표 ({API_PRICING["verified_on"]} 확인)</a></p>')
    columns = ["id", "model", "effort", "repeat", "status", "score", "max_score", "pass_rate", "total_tokens",
               API_COST_FIELD, "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
               "cache_ratio", "reasoning_ratio", "generation_seconds", "grading_seconds", "score_per_1000_tokens",
               "score_per_second", "error_type", "grading_error"]
    headers = [API_COST_LABEL if key == API_COST_FIELD else key for key in columns]
    parts.append(table(headers, [[flatten(row).get(k) for k in columns] for row in rows],
                       "runs", cost_column=columns.index(API_COST_FIELD)))
    parts.append('<h2>세부 채점 및 원본 기록</h2>')
    for row in rows:
        label = f'{row["model"]} / {row["effort"]} / #{row["repeat"]} — {row["status"]}'
        parts.append(f'<details><summary>{esc(label)}</summary>')
        path = row.get("artifact_path")
        if path:
            parts.append(artifact_links(path, ["events.jsonl", "stdout.log", "stderr.log", "final_message.md", "result.json", "submission/"]))
        tests = (row.get("grade") or {}).get("tests", [])
        parts.append(table(["테스트", "범주", "상태", "점수", "만점", "시간", "상세"], [[t.get(k) for k in ["name", "category", "status", "score", "max_score", "duration_seconds", "detail"]] for t in tests]))
        details = {k: row.get(k) for k in ["usage_source", "observed_configuration", "errors", "warnings", "process_error", "artifact_error", "submission_hashes"] if row.get(k)}
        parts.append(f'<pre>{html.escape(json.dumps(details, ensure_ascii=False, indent=2))}</pre></details>')
    parts.append('<footer>외부 폰트, CDN, 네트워크 요청 없이 동작합니다. 자체 테스트 출력은 원본 이벤트에 보존되며 독립 채점 점수로 인정하지 않습니다.</footer>')
    return "".join(parts) + f'<script>{SCRIPT}</script></body></html>'


def write_reports(output: Path, rows: list[dict], metadata: dict) -> None:
    synthetic = bool(metadata.get("synthetic"))

    def with_cost(row):
        return {**row, API_COST_FIELD: estimate_api_cost_usd(row, synthetic=synthetic)}

    rows = [with_cost(row) for row in rows]
    groups = summarize(rows)
    # Preserve abandoned attempts too, including measured token spend before an interruption.
    def history(pattern):
        records = {}
        for path in sorted(output.glob(pattern)):
            try:
                record = read_json(path)
            except (OSError, ValueError):
                continue
            if isinstance(record, dict):
                records[path] = record
        return records

    attempts_by_path = {path: with_cost(row) for path, row in history("runs/*/attempts/*/result.json").items()}
    attempts = list(attempts_by_path.values())
    experiment_cost = (summarize_experiment_cost([], {}) if metadata.get("grading_only")
                       else summarize_experiment_cost(rows, attempts_by_path))
    # Render every format before replacing any report. Invalid saved data must
    # not leave an old HTML report beside a partially regenerated JSON/CSV pair.
    json_text = json.dumps({"schema_version": 1, "generated_at": utc_now(), "metadata": metadata,
                           "api_pricing": API_PRICING, "experiment_cost": experiment_cost,
                           "results": rows, "summary": groups,
                           "attempt_history": attempts},
                          ensure_ascii=False, indent=2, allow_nan=False)
    flattened = [flatten(r) for r in rows]
    csv_text = None
    if flattened:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(flattened[0]))
        writer.writeheader()
        # CSV is often opened in Excel; model-controlled strings are data, never formulas.
        writer.writerows({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for k, v in row.items()} for row in flattened)
        csv_text = buffer.getvalue()
    html_text = render_html(rows, groups, metadata, experiment_cost)
    if metadata.get("synthetic"):
        html_text = html_text.replace('<h1>', '<p class="badge">샘플: 모든 점수·시간·토큰은 합성 데이터입니다. 실제 모델 비교 결과가 아닙니다.</p><h1>', 1)
    if attempts:
        attempt_table = table(["실험", "시도", "상태", "측정 토큰", API_COST_LABEL],
                        [[r.get("id"), r.get("attempt_id"), r.get("status"),
                          (r.get("usage") or {}).get("total_tokens"), r[API_COST_FIELD]]
                         for r in attempts], cost_column=4)
        html_text = html_text.replace("<footer>", '<h2>모든 시도 이력</h2><p>중단 후 재시도로 사용된 토큰도 각 시도 기록에 보존됩니다. 위 조합 통계는 각 실험의 현재 시도 기준입니다.</p>' + attempt_table + "<footer>")
    output.mkdir(parents=True, exist_ok=True)
    documents = [("results.json", json_text, "utf-8"), ("report.html", html_text, "utf-8")]
    if csv_text is not None:
        documents.append(("results.csv", csv_text, "utf-8-sig"))
    for name, content, encoding in documents:
        (output / (name + ".tmp")).write_text(content, encoding=encoding, newline="")
    for name, _, _ in documents:
        replace_file(output / (name + ".tmp"), output / name)
