#!/usr/bin/env python3
"""
ChainCheck GitHub Action entrypoint.

Reads PR description or commit messages from the GitHub event payload,
runs ChainCheck hallucination detection, posts a comment to the PR,
and exits non-zero if the score exceeds the threshold.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request


# ── GitHub helpers ─────────────────────────────────────────────────────────────

def _get_event() -> dict:
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _get_pr_description() -> tuple[str, int | None]:
    """Return (body, pr_number) from the GitHub event payload."""
    event = _get_event()
    pr = event.get("pull_request", {})
    return pr.get("body") or "", pr.get("number")


def _get_commit_messages(n: int = 10) -> tuple[str, None]:
    """Return recent commit messages joined as a single string."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "log", f"-{n}", "--pretty=format:%s%n%b"],
            text=True,
        )
        return out.strip(), None
    except Exception:
        return "", None


def _post_comment(token: str, repo: str, pr_number: int, body: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        print(f"::warning::ChainCheck could not post comment: {exc}")


def _set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT", "")
    if path:
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")
    else:
        print(f"::set-output name={name}::{value}")  # fallback for older runners


def _set_failed(msg: str) -> None:
    print(f"::error::{msg}")
    sys.exit(1)


# ── Comment builder ────────────────────────────────────────────────────────────

_LABEL_EMOJI = {
    "supported":    "✅",
    "unsupported":  "⚠️",
    "contradicted": "❌",
    "unknown":      "❓",
}
_RISK_EMOJI = {"low": "✅", "medium": "⚠️", "high": "❌"}


def _build_comment(result, score: float, threshold: float) -> str:
    risk = result.risk_level
    emoji = _RISK_EMOJI.get(risk, "❓")

    lines = [
        f"## {emoji} ChainCheck — score `{score:.2f}` ({risk.upper()})",
        "",
    ]

    if result.claim_details:
        lines += [
            "| Claim | Label | Conf | Evidence |",
            "|-------|-------|------|----------|",
        ]
        for cr in result.claim_details:
            le = _LABEL_EMOJI.get(cr.label, "❓")
            claim = cr.claim[:70].replace("|", "\\|")
            evidence = (cr.evidence or "—")[:60].replace("|", "\\|")
            lines.append(
                f"| {claim} | {le} {cr.label} | `{cr.confidence:.2f}` | {evidence} |"
            )
        lines.append("")

    if score >= threshold:
        lines.append(
            f"> ❌ **Failing** — score `{score:.2f}` exceeds threshold `{threshold}`. "
            "Review the flagged claims above before merging."
        )
    else:
        lines.append(
            f"> ✅ **Passing** — score `{score:.2f}` is below threshold `{threshold}`."
        )

    lines += [
        "",
        "<sub>Powered by [ChainCheck](https://github.com/pauti04/chaincheck) · "
        f"methods: {', '.join(result.method_results.keys())}</sub>",
    ]
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    openai_key   = os.environ.get("OPENAI_API_KEY", "")
    check_target = os.environ.get("INPUT_CHECK", "pr-description")
    threshold    = float(os.environ.get("INPUT_THRESHOLD", "0.7"))
    post_comment = os.environ.get("INPUT_POST_COMMENT", "true").lower() == "true"
    methods      = [m.strip() for m in os.environ.get("INPUT_METHODS", "judge").split(",")]
    context      = os.environ.get("INPUT_CONTEXT", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    repo         = os.environ.get("GITHUB_REPOSITORY", "")

    if not openai_key:
        _set_failed("openai-api-key is required but was not provided.")

    # ── Resolve text to check ──────────────────────────────────────────────────
    pr_number: int | None = None

    if check_target == "pr-description":
        text, pr_number = _get_pr_description()
        if not text:
            print("::notice::PR description is empty — nothing to check. Skipping.")
            _set_output("score", "0.0")
            _set_output("risk-level", "low")
            return
        print(f"Checking PR description ({len(text)} chars)…")

    elif check_target == "commit-messages":
        text, _ = _get_commit_messages()
        if not text:
            print("::notice::No commit messages found. Skipping.")
            _set_output("score", "0.0")
            _set_output("risk-level", "low")
            return
        # Derive PR number from event for posting comment
        event = _get_event()
        pr_number = event.get("pull_request", {}).get("number")
        print(f"Checking last commit messages ({len(text)} chars)…")

    else:
        # Treat check_target as literal text
        text = check_target
        print(f"Checking custom text ({len(text)} chars)…")

    # ── Run ChainCheck ─────────────────────────────────────────────────────────
    from chaincheck.detect import detect

    print(f"Running ChainCheck (methods={methods}, threshold={threshold})…")
    result = await detect(text, context=context, methods=methods)  # type: ignore[arg-type]

    score = result.aggregate_score
    risk  = result.risk_level
    print(f"Score: {score:.4f}  Risk: {risk.upper()}")

    _set_output("score", str(round(score, 4)))
    _set_output("risk-level", risk)

    # ── Post PR comment ────────────────────────────────────────────────────────
    if post_comment and github_token and repo and pr_number:
        comment = _build_comment(result, score, threshold)
        _post_comment(github_token, repo, int(pr_number), comment)
        print(f"Comment posted to PR #{pr_number}")
    elif post_comment and not github_token:
        print("::warning::post-comment=true but GITHUB_TOKEN is not set. Skipping comment.")

    # ── Exit code ──────────────────────────────────────────────────────────────
    if score >= threshold:
        _set_failed(
            f"Hallucination score {score:.2f} exceeds threshold {threshold} ({risk} risk). "
            "See PR comment for per-claim details."
        )
    else:
        print(f"✓ Passing — score {score:.2f} < threshold {threshold}")


if __name__ == "__main__":
    asyncio.run(main())
