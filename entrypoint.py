#!/usr/bin/env python3
"""
ChainCheck GitHub Action entrypoint.

Reads PR description or commit messages from the GitHub event payload,
runs ChainCheck hallucination detection, posts a comment to the PR,
and exits non-zero if the score exceeds the threshold.

Improvements over v1:
  - Passes git diff as context so judge can verify claims against actual code changes
  - Updates existing ChainCheck comment instead of posting a new one each push
  - Filters opinion/intent claims ("this PR adds X") before scoring
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import urllib.request

_COMMENT_TAG = "<!-- chaincheck-action -->"  # hidden tag to find and update our comment


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
    try:
        out = subprocess.check_output(
            ["git", "log", f"-{n}", "--pretty=format:%s%n%b"],
            text=True,
        )
        return out.strip(), None
    except Exception:
        return "", None


def _get_diff_from_api(token: str, repo: str, pr_number: int, max_chars: int = 6000) -> str:
    """
    Fetch PR file patches from the GitHub API.

    More reliable than git diff inside a Docker container since it doesn't
    depend on git remote config or network access to the origin.
    Skips lockfiles, minified JS, SVGs, and binary files.
    """
    _SKIP_EXTS = {".lock", ".min.js", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".woff", ".woff2"}
    _SKIP_DIRS = {"dist/", "build/", "node_modules/"}

    files = _github_api(token, "GET",
                        f"/repos/{repo}/pulls/{pr_number}/files?per_page=100")
    if not isinstance(files, list):
        return ""

    parts: list[str] = []
    for f in files:
        name  = f.get("filename", "")
        patch = f.get("patch", "")
        if not patch:
            continue
        if any(name.endswith(ext) for ext in _SKIP_EXTS):
            continue
        if any(name.startswith(d) for d in _SKIP_DIRS):
            continue
        parts.append(f"### {name}\n```diff\n{patch}\n```")

    diff = "\n\n".join(parts)
    if len(diff) > max_chars:
        diff = diff[:max_chars] + "\n\n[diff truncated]"
    return diff


def _github_api(token: str, method: str, path: str, body: dict | None = None) -> dict | list | None:
    """Thin wrapper around the GitHub REST API."""
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"::warning::GitHub API {method} {path} failed: {exc}")
        return None


def _upsert_comment(token: str, repo: str, pr_number: int, body: str) -> None:
    """
    Update the existing ChainCheck comment on this PR, or create one if absent.
    Uses a hidden HTML tag to identify our comment across pushes.
    """
    tagged_body = f"{_COMMENT_TAG}\n{body}"

    # Search existing comments for our tag
    comments = _github_api(token, "GET", f"/repos/{repo}/issues/{pr_number}/comments?per_page=100")
    if isinstance(comments, list):
        for c in comments:
            if _COMMENT_TAG in c.get("body", ""):
                # Update existing comment
                _github_api(token, "PATCH", f"/repos/{repo}/issues/comments/{c['id']}",
                            {"body": tagged_body})
                print(f"Updated existing ChainCheck comment ({c['id']})")
                return

    # No existing comment — create one
    _github_api(token, "POST", f"/repos/{repo}/issues/{pr_number}/comments",
                {"body": tagged_body})
    print(f"Posted new ChainCheck comment to PR #{pr_number}")


def _set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT", "")
    if path:
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")
    else:
        print(f"::set-output name={name}::{value}")


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


def _build_comment(result, score: float, threshold: float, used_diff: bool) -> str:
    risk  = result.risk_level
    emoji = _RISK_EMOJI.get(risk, "❓")
    ctx_note = " · verified against PR diff" if used_diff else " · no diff context"

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
            le    = _LABEL_EMOJI.get(cr.label, "❓")
            claim = cr.claim[:70].replace("|", "\\|")
            evid  = (cr.evidence or "—")[:70].replace("|", "\\|")
            lines.append(f"| {claim} | {le} {cr.label} | `{cr.confidence:.2f}` | {evid} |")
        lines.append("")

    if score >= threshold:
        lines.append(
            f"> ❌ **Failing** — score `{score:.2f}` exceeds threshold `{threshold}`. "
            "Review flagged claims above before merging."
        )
    else:
        lines.append(
            f"> ✅ **Passing** — score `{score:.2f}` is below threshold `{threshold}`."
        )

    lines += [
        "",
        f"<sub>Powered by [ChainCheck](https://github.com/pauti04/chaincheck){ctx_note} · "
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
    context      = os.environ.get("INPUT_CONTEXT", "")   # user-supplied override
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
        pr_number = _get_event().get("pull_request", {}).get("number")
        print(f"Checking commit messages ({len(text)} chars)…")

    else:
        text = check_target
        print(f"Checking custom text ({len(text)} chars)…")

    # ── Build context: user override → PR diff (API) → empty ─────────────────
    used_diff = False
    if not context.strip() and github_token and repo and pr_number:
        diff = _get_diff_from_api(github_token, repo, int(pr_number))
        if diff:
            context = f"Changes in this PR:\n\n{diff}"
            used_diff = True
            print(f"Using PR diff as context ({len(diff)} chars)…")
        else:
            print("No diff available — running in fact-check mode…")
    elif not context.strip():
        print("No token/repo/PR — running in fact-check mode…")

    # ── Run ChainCheck ─────────────────────────────────────────────────────────
    from chaincheck.detect import detect

    print(f"Running ChainCheck (methods={methods}, threshold={threshold})…")
    result = await detect(text, context=context, methods=methods)  # type: ignore[arg-type]

    score = result.aggregate_score
    risk  = result.risk_level
    print(f"Score: {score:.4f}  Risk: {risk.upper()}")

    _set_output("score", str(round(score, 4)))
    _set_output("risk-level", risk)

    # ── Post / update PR comment ───────────────────────────────────────────────
    if post_comment and github_token and repo and pr_number:
        comment = _build_comment(result, score, threshold, used_diff)
        _upsert_comment(github_token, repo, int(pr_number), comment)
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
