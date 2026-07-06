---
description: Run the full TDD agent pipeline (tdd → architect → coder → reviewer → guide) end-to-end on a feature or bugfix.
argument-hint: <feature or bugfix to build>
---

Run the **TDD pipeline** on: $ARGUMENTS

Execute these stages **in order**, each dispatched as its named subagent, passing the prior stage's output to the next as context. Do NOT skip a stage. Work on a dedicated branch (`feature/<slug>`, `fix/<slug>`, `chore/<slug>`), never `main`.

1. **tdd** — write the failing tests (RED) that capture the requirements. Confirm each fails for the right reason (missing symbol / assertion failure — never a syntax or import error). Only the RED phase; no implementation.
2. **architect** — design the implementation blueprint (files to create/modify, interfaces, data flow, build sequence) that will make those tests pass. No production code.
3. **coder** — implement strictly to the blueprint, turning the tests GREEN. Do not redesign; follow the architecture.
4. **reviewer** — audit the tests + architecture + code together; produce a structured list of findings with severities. Read-only, no edits.
5. **guide** — verify each finding independently. Fix the real ones directly. For any finding it judges false, dispatch **grunty-reviewer** for a strict TRUE/FALSE second-pass verdict.

If the request is a non-trivial new feature (not a small fix), run brainstorming → writing-plans first so the pipeline has a spec + plan to build against.

After the pipeline:
- Verify the whole suite passes (evidence, not assertion).
- Open a **PR to `main`**, squash-merge, then archive the branch with an annotated `archive/<branch>` tag.

Stop and report to the user if any stage is blocked, the tests can't be made to pass, or a design decision genuinely needs their input — don't guess past a real fork.
