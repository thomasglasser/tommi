# T.O.M.M.I. (Tommy's Online Minecraft Modding Intelligence) 🤖

**T.O.M.M.I.** is an autonomous, opinionated AI code reviewer and feedback learner for GitHub Pull Requests. It automatically inspects PR diffs against strict coding standards, architectural rules, side-safety constraints, and performance guidelines, while continuously learning and refining its rulebase from maintainer feedback.

---

## ✨ Features

- **🎯 Opinionated Review Engine**: Deeply understands Java, Minecraft, NeoForge, collections, side-safety, tick performance, and architectural patterns.
- **🔄 Autonomous Learning Loop**: Maintainers can teach T.O.M.M.I. directly on PR comments (`/tommi learn ...` or `/tommi false-positive ...`), and T.O.M.M.I. will automatically formulate and submit a Pull Request to update its central rules!
- **📚 Modular & Hierarchical Rules**:
  - Global base rules hosted centrally in `rules/*.md`.
  - Project-level rules discovered automatically from `TOMMI.md`, `AGENTS.md`, or `.agents/rules/` in client repositories.
- **📍 Resilient Inline Reviews**: Accurately places comments on specific right-side diff lines with automated hunk mapping and fallback handling.
- **⚡ Reusable GitHub Action**: Integrates into any repository with just a tiny ~15 line workflow file.

---

## 🚀 Quick Start & Integration

Add the following workflow to your repository at `.github/workflows/tommi.yml`:

```yaml
name: T.O.M.M.I. Code Reviewer

on:
  issue_comment:
    types: [created, edited]
  pull_request_review_comment:
    types: [created, edited]
  pull_request_review:
    types: [submitted, edited]
  pull_request:
    types: [closed]

jobs:
  tommi:
    # Trigger on human comments/reviews containing /tommi OR when a PR is merged
    if: |
      (github.event_name == 'pull_request' && github.event.pull_request.merged == true) ||
      ((github.event.comment.user.type != 'Bot' && github.event.review.user.type != 'Bot' && !endsWith(github.actor, '[bot]')) &&
       (contains(github.event.comment.body, '/tommi') || contains(github.event.review.body, '/tommi')))
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
      issues: write

    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Run T.O.M.M.I.
        uses: thomasglasser/tommi@main
        with:
          app-id: ${{ secrets.TOMMI_APP_ID }}
          private-key: ${{ secrets.TOMMI_PRIVATE_KEY }}
          gemini-api-key: ${{ secrets.GEMINI_API_KEY }}
```

---

## 💬 Slash Commands

Maintainers and contributors can interact with T.O.M.M.I. directly in PR comment threads:

| Command | Description | Example |
| :--- | :--- | :--- |
| `/tommi review` | Runs a complete code review on the PR against all global and repository rules. | `/tommi review` |
| `/tommi learn <rule>` | Teaches T.O.M.M.I. a new rule or preference. Automatically opens a PR against `thomasglasser/tommi`. | `/tommi learn Always prefer custom Holder wrappers for entity data.` |
| `/tommi false-positive <reason>` | Explains why a comment was inaccurate so T.O.M.M.I. can add a nuanced exception rule. | `/tommi false-positive In this case, copying the list is required for concurrency.` |

---

## 📂 Rule Structure

The central rules are organized under the `rules/` directory:

- [`rules/core.md`](./rules/core.md): Naming conventions, class layout, control flow, DRY, and review etiquette.
- [`rules/java.md`](./rules/java.md): FastUtil collections, immutability, stream avoidance, generics, no `var`.
- [`rules/minecraft.md`](./rules/minecraft.md): Client/Server separation, data attachments, tags, constants, GUI rendering.
- [`rules/performance.md`](./rules/performance.md): Zero object allocations in tick loops, `BlockPos.Mutable`, throttling.

### Project-Specific Rules
If your repository contains a `TOMMI.md` or `AGENTS.md` file (or `.agents/rules/*.md`), T.O.M.M.I. automatically parses and respects those rules alongside the global ones!

---

## 📄 License

MIT License. Copyright (c) Thomas Glasser.
