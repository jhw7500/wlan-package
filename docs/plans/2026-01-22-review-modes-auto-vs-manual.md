# Auto vs Manual Review Modes (Workflow Config) Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `.github/workflow-config.yml` 한 곳에서 "자동 리뷰(auto)" / "수동 리뷰(manual)" 모드를 전환하고, 수동 모드에서는 PR에 자동으로 리뷰가 달리지 않으며 PR 코멘트의 멘션/커맨드로만 리뷰가 실행되도록 한다.

**현재 상태(중요):**
- `projects/wlan-package/.github/workflows/Gemini Auto PR Review`는 PR opened/synchronize에서 자동 리뷰를 돌린다.
- `projects/wlan-package/.github/workflows/Claude Code Review`도 PR opened/synchronize에서 자동 리뷰를 돌린다.
- 추가로 `Gemini Dispatch`는 `pull_request.opened` 이벤트에서 **자동으로 review 커맨드로 분기**한다(= 수동 모드에서 이것도 꺼야 "자동 리뷰 없음"이 성립).

**Architecture:**
- Auto mode: 지금처럼 자동 리뷰가 PR에 달린다.
- Manual mode:
  - PR opened/synchronize 시 자동 리뷰 워크플로우는 실행되더라도 "skip" 처리(리뷰 코멘트 남기지 않음).
  - `@claude` / `@gemini` (또는 `@gemini-cli`) 멘션/커맨드로만 리뷰 실행.
- 설정은 `.github/workflow-config.yml`에 저장하고, 워크플로우에서 이 값을 읽어 분기한다.

**Tech Stack:** GitHub Actions, `gh` CLI, (필요 시) Ruby stdlib `yaml`, 공유 워크플로우 `jhw7500/automation`.

## Requirements

- Single source of truth: `.github/workflow-config.yml`.
- Two modes:
  - `auto`: PR opened/synchronize 시 자동 리뷰가 동작(현재와 동일).
  - `manual`: PR opened/synchronize에서는 자동 리뷰가 아무것도 남기지 않음. 명시적 멘션/커맨드로만 리뷰 실행.
- Manual request는 PR thread에서 동작해야 함(PR 코멘트는 `issue_comment` 이벤트).
- `workflow_dispatch` 기반 수동 실행(예: `🧪 Gemini Manual PR Review`)은 유지.
- 수동 트리거 권한은 제한(협업자/오너/allowlist 등).

## Config Shape (proposed)

Modify `.github/workflow-config.yml`:

```yaml
review:
  mode: auto # auto | manual

  # auto mode에서도 수동 멘션 트리거를 허용할지
  allow_manual_triggers: true

  # 누가 수동 멘션 트리거를 할 수 있는지
  manual_trigger:
    allow_issue_author: true
    allow_repo_collaborators: true
    allowlist_users: []

  reviewers:
    claude:
      enabled: true
      auto_on_pr_open: true
    gemini:
      enabled: true
      auto_on_pr_open: true
```

Notes:
- 기본값은 `auto`로 유지(기존 동작 보존).
- `auto_on_pr_open`은 특히 Gemini 쪽에서 중요: 현재 `Gemini Dispatch`가 PR opened를 자동 review로 분기하고 있기 때문.

## Task 1: Add config reader helper

**Files:**
- Create: `projects/wlan-package/.github/scripts/read-workflow-config.rb`

**Step 1: Read YAML and print key outputs**

Implement a Ruby script that:
- loads `.github/workflow-config.yml`
- prints a small set of values in `KEY=VALUE` form (safe for Actions):
  - `REVIEW_MODE`
  - `ALLOW_MANUAL_TRIGGERS`
  - `CLAUDE_ENABLED`
  - `CLAUDE_AUTO_ON_PR_OPEN`
  - `GEMINI_ENABLED`
  - `GEMINI_AUTO_ON_PR_OPEN`

Example output:

```text
REVIEW_MODE=manual
ALLOW_MANUAL_TRIGGERS=true
CLAUDE_ENABLED=true
GEMINI_ENABLED=true
```

**Step 2: Verification**

Run locally:

```bash
ruby .github/scripts/read-workflow-config.rb
```

Expected: prints the keys above.

## Task 2: Gate automatic review workflows

**Files:**
- Modify: `projects/wlan-package/.github/workflows/gemini-auto-review.yml`
- Modify: `projects/wlan-package/.github/workflows/claude-code-review.yml`

**Step 1: Add early "config gate" job/step**

각 자동 리뷰 workflow의 첫 job에서:
- checkout
- `ruby .github/scripts/read-workflow-config.rb` 실행 후 job outputs로 export
- 아래 조건이면 리뷰 job을 skip:
  - `REVIEW_MODE == manual`
  - 또는 해당 reviewer의 `*_AUTO_ON_PR_OPEN == false`

Implementation approach:
- first job `config`가 outputs를 셋업
- downstream reusable-workflow job에 `if:`를 붙여 skip

**Step 2: Verification**

Set `review.mode: manual` and open a PR.
- Expected: workflows either skip or complete without posting a review comment.

## Task 3: Ensure manual review triggers work (mentions/commands)

**Files:**
- Modify (likely): `projects/automation/.github/workflows/gemini-dispatch.yml`
- (Optional) Modify: `projects/wlan-package/.github/workflows/gemini-dispatch.yml`

**핵심 문제:** 현재 `Gemini Dispatch`는 `pull_request.opened`에서 자동으로 review 커맨드로 분기한다.

**Step 1: Gemini Dispatch에 review mode 반영**
- `pull_request.opened` 이벤트일 때:
  - `review.mode=manual` 또는 `review.reviewers.gemini.auto_on_pr_open=false`이면 `command=noop`로 바꾸고 리뷰를 실행하지 않도록 한다.
- 대신 PR 코멘트에서만 수동 리뷰가 실행되도록 유지한다.
  - 기존: `@gemini-cli /review` 또는 `@gemini-cli ...` 형태
  - 요구사항 반영: `@gemini /review` 같은 alias를 추가할지 결정(옵션)

**Step 2: Claude 수동 리뷰 유지**
- `projects/wlan-package/.github/workflows/claude.yml`은 `issue_comment`를 이미 받고 있으므로 manual 모드에서도 유지.
- auto 리뷰만 끄면 됨(`Claude Code Review` gating).

**How to run the reviewer workflows**

Gemini:
- 수동 요청은 `Gemini Dispatch`가 이미 커맨드를 추출해 `review` job을 실행하는 구조. 이 경로를 유지하되 PR opened 자동 경로만 차단.

Claude:
- `@claude` 멘션 경로(현재 동작)는 유지.

Option B: call `workflow_dispatch` workflows via `gh workflow run`.

**Verification**

With `review.mode: manual`:
- Create PR
- Comment `@gemini`
- Expect: Gemini review appears
- Comment `@claude`
- Expect: Claude review appears

## Task 4: Update docs

**Files:**
- Modify: `projects/wlan-package/README.md` (or add `docs/` page)

Document:
- how to switch modes (GitHub UI + config file)
- how to request manual review (`@claude`, `@gemini`)
- who can trigger (permissions)

## Rollout Plan

1) `.github/workflow-config.yml`에 `review.mode` 및 reviewer flags 추가(기본 `auto`).
2) `Gemini Auto PR Review` / `Claude Code Review` workflow에서 config gate 구현.
3) `Gemini Dispatch`에서 PR opened 자동 review 분기를 config로 제어.
4) `review.mode: manual`로 전환 후:
   - PR opened/synchronize에서 자동 리뷰가 더 이상 안 달리는지 확인
   - PR 코멘트로 `@claude`, `@gemini-cli /review`가 정상 동작하는지 확인

## Open Questions

- manual mode에서 Gemini 수동 트리거 문법을 `@gemini`로도 지원할지?
  - 옵션 A: 기존 `@gemini-cli`만 유지(안전/명확)
  - 옵션 B: `@gemini` alias 추가(요청 UX에 더 가까움)

- 수동 트리거 권한 정책:
  - 협업자만 허용 vs 이슈/PR 작성자도 허용
