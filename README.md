# ChainCheck Action

[![GitHub Marketplace](https://img.shields.io/badge/Marketplace-ChainCheck-blue?logo=github)](https://github.com/marketplace/actions/chaincheck-llm-hallucination-detector)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Detect hallucinations in AI-generated PR descriptions and commit messages.**

Uses [ChainCheck](https://github.com/pauti04/chaincheck) to decompose text into atomic claims, verify each one, and post a per-claim verdict as a PR comment. Fails the check if the hallucination score exceeds a configurable threshold.

---

## Example output

> ## ❌ ChainCheck — score `0.71` (HIGH)
>
> | Claim | Label | Conf | Evidence |
> |-------|-------|------|----------|
> | This PR reduces API latency by 40% | ⚠️ unsupported | `0.89` | no benchmark data provided |
> | Adds async batching to the NLI pipeline | ✅ supported | `0.94` | — |
> | Fixes the rate-limit regression from #142 | ⚠️ unsupported | `0.81` | issue #142 not referenced |
>
> ❌ **Failing** — score `0.71` exceeds threshold `0.7`. Review the flagged claims above before merging.

---

## Usage

```yaml
# .github/workflows/chaincheck.yml
name: ChainCheck

on:
  pull_request:
    types: [opened, edited, synchronize]

jobs:
  hallucination-check:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write   # needed to post the comment

    steps:
      - uses: pauti04/chaincheck-action@v1.4
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}  # enables diff context
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
```

> No `actions/checkout` step needed — the action fetches the PR diff directly from the GitHub API.

---

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `openai-api-key` | ✅ | — | OpenAI API key for the judge model |
| `check` | | `pr-description` | What to check: `pr-description`, `commit-messages`, or any custom string |
| `threshold` | | `0.7` | Fail if hallucination score ≥ this value (0.0–1.0) |
| `post-comment` | | `true` | Post results as a PR comment |
| `methods` | | `judge` | Detection methods: `nli`, `judge`, or `nli,judge` |
| `context` | | `""` | Optional reference doc to check claims against |

## Outputs

| Output | Description |
|--------|-------------|
| `score` | Aggregate hallucination score (0.0–1.0) |
| `risk-level` | `low`, `medium`, or `high` |

---

## Examples

**Check PR description (default):**
```yaml
- uses: pauti04/chaincheck-action@v1.4
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
    threshold: '0.7'
    post-comment: 'true'
```

**Check commit messages** (requires `actions/checkout` first):
```yaml
- uses: actions/checkout@v4   # required for commit-messages mode
  with:
    fetch-depth: 10

- uses: pauti04/chaincheck-action@v1.4
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
    check: commit-messages
    threshold: '0.8'
```

**NLI + judge ensemble (higher accuracy, slower):**
```yaml
- uses: pauti04/chaincheck-action@v1.4
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
    methods: 'nli,judge'
    threshold: '0.65'
```

**Use score in downstream steps:**
```yaml
- id: chaincheck
  uses: pauti04/chaincheck-action@v1.4
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}

- name: Print score
  run: echo "Score=${{ steps.chaincheck.outputs.score }} Risk=${{ steps.chaincheck.outputs.risk-level }}"
```

**Soft mode — comment only, never fail:**
```yaml
- uses: pauti04/chaincheck-action@v1.4
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
    threshold: '1.1'   # effectively disables failing
    post-comment: 'true'
```

---

## How it works

1. Reads the PR description (or commit messages) from the GitHub event payload
2. **Fetches the PR diff via the GitHub API** and uses it as grounding context for claim verification — no `actions/checkout` required
3. Decomposes text into atomic claims using `gpt-4o-mini`
4. Verifies each claim with the selected method (NLI cross-encoder and/or LLM judge)
5. **Upserts a single PR comment** — edits the previous ChainCheck comment on re-runs instead of posting a new one each push
6. Exits non-zero if `aggregate_score ≥ threshold`

Powered by [ChainCheck](https://github.com/pauti04/chaincheck) — achieves **79% F1 / 94% precision** on HaluEval-QA.

---

## Cost

Each run makes 1–3 OpenAI API calls (claim decomposition + judge). A typical PR description (200 words) costs ~$0.001 with `gpt-4o-mini`.

---

## License

MIT
