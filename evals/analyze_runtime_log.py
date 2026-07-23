# -*- coding: utf-8 -*-
"""Turn runtime logs into repeatable conversation-engineering metrics."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
import statistics


LINE_RE = re.compile(r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]\[([^\]]+)\]")
PAIR_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
MODEL_RE = re.compile(
    r"\[ModelGateway\] completed component=([^\s]+) purpose=([^\s]+).*?elapsed=([0-9.]+)s"
)
MEMORY_RE = re.compile(r"\[LongTermMemory\] injected (\d+) memories plan=([^\s]+)")
SKIP_RE = re.compile(r"\[LongTermMemory\] retrieval skipped plan=([^\s]+)")
PLAN_RE = re.compile(r"context pack compiled .*?tokens=(\d+)/(\d+) plan=([^\s]+)")
GOVERN_RE = re.compile(
    r"governance completed .*?extracted=(\d+) eligible=(\d+) written=(\d+) failed=(\d+)"
)
NATIVE_HISTORY_RE = re.compile(r"native history prepared messages=(\d+)")
CRITIC_RE = re.compile(r"selective critic status=([^\s]+) risks=(\d+)")
RECENT_STATE_RE = re.compile(r"\[RecentState\] (updated|no grounded update|update skipped)")
ARCHIVE_MESSAGE_RE = re.compile(r"\[ConversationArchive\] message archived")
ARCHIVE_BACKUP_RE = re.compile(r"\[ConversationArchive\] backup completed")
EPISODE_READY_RE = re.compile(r"\[EpisodeBuilder\] episode ready")
EPISODIC_RE = re.compile(r"\[EpisodicMemory\] retrieved episodes=(\d+) mode=([^\s]+)")


def _timestamp(line):
    match = LINE_RE.search(line)
    if not match:
        return None
    value = match.group(2).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    return None


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 3)


def _latency_summary(values):
    values = [float(value) for value in values if value is not None and value >= 0]
    return {
        "samples": len(values),
        "mean_seconds": round(statistics.mean(values), 3) if values else None,
        "median_seconds": round(statistics.median(values), 3) if values else None,
        "p95_seconds": _percentile(values, 0.95),
        "max_seconds": round(max(values), 3) if values else None,
    }


def analyze_text(text):
    traces = defaultdict(lambda: {"starts": [], "completions": []})
    levels = Counter()
    plans = Counter()
    memory_plans = Counter()
    model_elapsed = defaultdict(list)
    token_utilization = []
    memory_injected = []
    governance = Counter()
    stale_events = 0
    native_history_messages = []
    critic_statuses = Counter()
    critic_risk_turns = 0
    recent_state_statuses = Counter()
    archive_messages = 0
    archive_backups = 0
    episodes_ready = 0
    episodic_retrievals = []
    episodic_modes = Counter()

    for line in str(text or "").splitlines():
        level_match = LINE_RE.search(line)
        if level_match:
            levels[level_match.group(1)] += 1
        ts = _timestamp(line)

        if "[Trace]" in line:
            pairs = dict(PAIR_RE.findall(line))
            trace_id = pairs.get("trace_id", "")
            stage = pairs.get("stage", "")
            if trace_id and trace_id != "-" and ts is not None:
                trace = traces[trace_id]
                if stage == "input_received":
                    trace["input"] = min(ts, trace.get("input", ts))
                elif stage == "outbound_started":
                    trace["starts"].append(ts)
                elif stage == "outbound_completed":
                    trace["completions"].append(ts)
                    try:
                        trace["sent_parts"] = max(
                            int(pairs.get("sent_parts", "0")),
                            int(trace.get("sent_parts", 0)),
                        )
                    except ValueError:
                        pass
                if stage == "input_stale" or pairs.get("stale", "").lower() == "true":
                    stale_events += 1

        model_match = MODEL_RE.search(line)
        if model_match:
            component, purpose, elapsed = model_match.groups()
            model_elapsed["%s:%s" % (component, purpose)].append(float(elapsed))

        memory_match = MEMORY_RE.search(line)
        if memory_match:
            memory_injected.append(int(memory_match.group(1)))
            memory_plans[memory_match.group(2)] += 1
        skip_match = SKIP_RE.search(line)
        if skip_match:
            memory_plans[skip_match.group(1) + ":skipped"] += 1

        plan_match = PLAN_RE.search(line)
        if plan_match:
            used, maximum, mode = plan_match.groups()
            plans[mode] += 1
            if int(maximum) > 0:
                token_utilization.append(int(used) / float(maximum))

        govern_match = GOVERN_RE.search(line)
        if govern_match:
            extracted, eligible, written, failed = map(int, govern_match.groups())
            governance.update({
                "turns": 1,
                "extracted": extracted,
                "eligible": eligible,
                "written": written,
                "failed": failed,
            })

        native_match = NATIVE_HISTORY_RE.search(line)
        if native_match:
            native_history_messages.append(int(native_match.group(1)))
        critic_match = CRITIC_RE.search(line)
        if critic_match:
            status, risk_count = critic_match.groups()
            critic_statuses[status] += 1
            if int(risk_count) > 0:
                critic_risk_turns += 1
        recent_state_match = RECENT_STATE_RE.search(line)
        if recent_state_match:
            recent_state_statuses[recent_state_match.group(1)] += 1
        if ARCHIVE_MESSAGE_RE.search(line):
            archive_messages += 1
        if ARCHIVE_BACKUP_RE.search(line):
            archive_backups += 1
        if EPISODE_READY_RE.search(line):
            episodes_ready += 1
        episodic_match = EPISODIC_RE.search(line)
        if episodic_match:
            episodic_retrievals.append(int(episodic_match.group(1)))
            episodic_modes[episodic_match.group(2)] += 1

    first_ready = []
    all_sent = []
    bubbles = []
    completed_turns = 0
    for trace in traces.values():
        input_ts = trace.get("input")
        if input_ts is None:
            continue
        if trace["starts"]:
            first_ready.append(min(trace["starts"]) - input_ts)
        if trace["completions"]:
            all_sent.append(max(trace["completions"]) - input_ts)
            completed_turns += 1
        if trace.get("sent_parts") is not None:
            bubbles.append(int(trace["sent_parts"]))

    model_report = {
        key: _latency_summary(values)
        for key, values in sorted(model_elapsed.items())
    }
    return {
        "schema_version": 1,
        "turns": {
            "input_traces": sum(1 for trace in traces.values() if trace.get("input") is not None),
            "completed": completed_turns,
            "stale_events": stale_events,
        },
        "latency": {
            "input_to_outbound_start": _latency_summary(first_ready),
            "input_to_all_sent": _latency_summary(all_sent),
            "model_calls": model_report,
        },
        "delivery": {
            "bubble_samples": len(bubbles),
            "mean_bubbles": round(statistics.mean(bubbles), 3) if bubbles else None,
            "max_bubbles": max(bubbles) if bubbles else None,
        },
        "context": {
            "plans": dict(sorted(plans.items())),
            "memory_plans": dict(sorted(memory_plans.items())),
            "mean_token_utilization": round(statistics.mean(token_utilization), 4) if token_utilization else None,
            "max_token_utilization": round(max(token_utilization), 4) if token_utilization else None,
            "mean_memories_injected": round(statistics.mean(memory_injected), 3) if memory_injected else 0.0,
            "native_history_samples": len(native_history_messages),
            "mean_native_history_messages": (
                round(statistics.mean(native_history_messages), 3)
                if native_history_messages else 0.0
            ),
            "episodic_retrieval_samples": len(episodic_retrievals),
            "mean_episodes_retrieved": (
                round(statistics.mean(episodic_retrievals), 3)
                if episodic_retrievals else 0.0
            ),
            "episodic_modes": dict(sorted(episodic_modes.items())),
        },
        "conversation_control": {
            "critic_statuses": dict(sorted(critic_statuses.items())),
            "critic_risk_turns": critic_risk_turns,
            "recent_state_statuses": dict(sorted(recent_state_statuses.items())),
        },
        "memory_governance": dict(governance),
        "conversation_archive": {
            "messages_archived": archive_messages,
            "episodes_ready": episodes_ready,
            "backups_completed": archive_backups,
        },
        "log_health": {
            "warnings": levels["WARNING"],
            "errors": levels["ERROR"] + levels["CRITICAL"],
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Analyze Xiaoyou runtime logs")
    parser.add_argument("logfile")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    report = analyze_text(Path(args.logfile).read_text(encoding="utf-8", errors="replace"))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
