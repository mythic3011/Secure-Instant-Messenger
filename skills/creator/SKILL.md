---
name: creator
description: Use when creating a new project artifact, skill, knowledge file, AI config, or other reusable context and you must decide the correct scope and destination first.
---

# Creator

## Overview

Use this skill before creating new repo-owned context or process artifacts. Its
job is to decide scope first, then place the artifact in the correct visible
canonical location.

## When to Use

- Creating a new skill
- Creating a new knowledge file or project knowledge base
- Creating a new AI config or agent helper file
- Deciding whether something belongs to this repo, another repo, or personal-only scope
- Cleaning up misplaced AI/process files

Do not create a new file until scope and destination are explicit.

## Scope Decision

Choose one scope first:

### 1. Project Scope

Use when the artifact is specific to this repo and should travel with the code.

Put it in:

```text
skills/      repo-local reusable skill
knowledge/   repo-local reusable knowledge or project memory
ai/          AI-only setup, sync, or local agent helper material
```

### 2. Global / Cross-Project Scope

Use when the artifact is reusable across multiple repos and should not live only
in this project.

Rule:

- do not bury cross-project material in this repo unless the user explicitly wants a local copy
- prefer keeping only a minimal reference or compatibility note here

### 3. Personal / Private Scope

Use when the artifact contains personal workflow details, local machine setup,
or material that should not be committed with the product repo.

Rule:

- keep it out of normal product directories
- if it must live in this repo locally, place it under `ai/` and ensure it is ignored

### 4. Other Repo / External Scope

Use when the artifact belongs to another codebase, another product, or a user’s
shared personal toolkit rather than this repo.

Rule:

- do not create it here by default
- state that the scope is external and ask or note the boundary explicitly

## Placement Rules

```text
New skill                     -> skills/<name>/SKILL.md
Global repo knowledge         -> knowledge/global/<topic>.md
Project-specific knowledge    -> knowledge/projects/<project>/PROJECT.md
AI-only repo helper           -> ai/<file>
Compatibility mirror          -> hidden paths only after syncing from visible source
```

Never make hidden directories the canonical source.

For repo-local skills, the default bundle should usually be:

```text
skills/<name>/SKILL.md
skills/<name>/agents/openai.yaml
skills/<name>/references/<topic>.md   (only when the workflow benefits from reusable checklists/templates)
```

Do not add extra files by habit. Add `agents/openai.yaml` when you want the
skill to surface cleanly in UI/tooling, and add short references only when they
remove repeated prompt boilerplate.

## Creation Flow

1. Identify what is being created
2. Decide scope: project / global / personal / external
3. Pick the canonical visible destination
4. Create the artifact there
5. If creating a skill, add the minimal supporting bundle:
   `agents/openai.yaml` and only the reference files that earn their keep
6. Update registry docs if the new artifact is part of the tracked AI context
7. Sync compatibility mirrors only if needed

## Common Mistakes

- Creating files before deciding scope
- Putting AI-only helper files in `docs/` or `scripts/`
- Treating hidden compatibility directories as the source of truth
- Storing personal-only agent material in product-facing repo paths
- Creating a repo-local artifact for something that clearly belongs to another scope
