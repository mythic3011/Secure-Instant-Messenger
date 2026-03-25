#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_SRC_DIR="$ROOT_DIR/skills"
KNOWLEDGE_SRC_DIR="$ROOT_DIR/knowledge"
SETTINGS_SRC_FILE="$ROOT_DIR/ai/settings.local.json"
CLAUDE_SRC_FILE="$ROOT_DIR/CLAUDE.md"
AGENTS_DST_DIR="$ROOT_DIR/.agents/skills"
CLAUDE_DST_DIR="$ROOT_DIR/.claude"
CLAUDE_SKILLS_DST_DIR="$CLAUDE_DST_DIR/skills"
CLAUDE_KNOWLEDGE_DST_DIR="$CLAUDE_DST_DIR/knowledge"
MODE="${1:-copy}"

if [[ ! -d "$SKILLS_SRC_DIR" ]]; then
  echo "[sync-agent-context] missing source directory: $SKILLS_SRC_DIR" >&2
  exit 1
fi

case "$MODE" in
  copy|symlink)
    ;;
  *)
    echo "[sync-agent-context] invalid mode: $MODE (use: copy | symlink)" >&2
    exit 1
    ;;
esac

if [[ "$MODE" == "symlink" ]]; then
  if [[ "${OS:-}" == "Windows_NT" ]] || uname | grep -qiE 'mingw|msys|cygwin'; then
    echo "[sync-agent-context] symlink mode is not portable on this Windows-like environment; use copy mode" >&2
    exit 1
  fi
fi

mkdir -p "$AGENTS_DST_DIR" "$CLAUDE_DST_DIR"

find "$AGENTS_DST_DIR" -mindepth 1 -maxdepth 1 \( -type d -o -type l \) | while read -r dir; do
  name="$(basename "$dir")"
  if [[ ! -d "$SKILLS_SRC_DIR/$name" ]]; then
    rm -rf "$dir"
  fi
done

find "$SKILLS_SRC_DIR" -mindepth 1 -maxdepth 1 -type d | while read -r src_subdir; do
  name="$(basename "$src_subdir")"
  dst_subdir="$AGENTS_DST_DIR/$name"
  rm -rf "$dst_subdir"
  if [[ "$MODE" == "symlink" ]]; then
    ln -s "$src_subdir" "$dst_subdir"
  else
    mkdir -p "$dst_subdir"
    cp "$src_subdir/SKILL.md" "$dst_subdir/SKILL.md"
  fi
done

rm -rf "$CLAUDE_SKILLS_DST_DIR" "$CLAUDE_KNOWLEDGE_DST_DIR"

if [[ "$MODE" == "symlink" ]]; then
  ln -s "$SKILLS_SRC_DIR" "$CLAUDE_SKILLS_DST_DIR"
  ln -s "$KNOWLEDGE_SRC_DIR" "$CLAUDE_KNOWLEDGE_DST_DIR"
else
  mkdir -p "$CLAUDE_SKILLS_DST_DIR" "$CLAUDE_KNOWLEDGE_DST_DIR"
  find "$SKILLS_SRC_DIR" -mindepth 1 -maxdepth 1 -type d | while read -r src_subdir; do
    name="$(basename "$src_subdir")"
    mkdir -p "$CLAUDE_SKILLS_DST_DIR/$name"
    cp "$src_subdir/SKILL.md" "$CLAUDE_SKILLS_DST_DIR/$name/SKILL.md"
  done
  cp -R "$KNOWLEDGE_SRC_DIR"/. "$CLAUDE_KNOWLEDGE_DST_DIR"/
fi

if [[ -f "$SETTINGS_SRC_FILE" ]]; then
  cp "$SETTINGS_SRC_FILE" "$CLAUDE_DST_DIR/settings.local.json"
fi

if [[ -f "$CLAUDE_SRC_FILE" ]]; then
  cp "$CLAUDE_SRC_FILE" "$CLAUDE_DST_DIR/CLAUDE.md"
fi

echo "[sync-agent-context] mirrored visible canonical AI context into hidden compatibility paths (mode: $MODE)"
