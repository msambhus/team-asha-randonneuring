#!/usr/bin/env bash
# Ralph Wiggum Loop — Team Asha Randonneuring Chatbot
# Each iteration: fresh Claude context, state lives in files + git
#
# Usage:
#   ./ralph.sh          # Run 10 iterations (default)
#   ./ralph.sh 20       # Run 20 iterations
#   ./ralph.sh 5 --dry  # Dry run (show what would happen)

set -euo pipefail

# ─── Config ──────────────────────────────────────────────────────────
MAX_ITERATIONS=${1:-10}
DRY_RUN=${2:-""}
PROMPT_FILE="PROMPT.md"
ACTIVITY_LOG="activity.md"
COOLDOWN=5  # seconds between iterations

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ─── Helpers ─────────────────────────────────────────────────────────
timestamp() {
  date "+%Y-%m-%d %H:%M:%S"
}

log() {
  echo -e "${DIM}[$(timestamp)]${NC} $1"
}

banner() {
  echo ""
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${BOLD}$1${NC}"
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""
}

divider() {
  echo -e "${DIM}──────────────────────────────────────────────────────────────${NC}"
}

# ─── Pre-flight checks ──────────────────────────────────────────────
if ! command -v claude &> /dev/null; then
  echo -e "${RED}Error: 'claude' CLI not found. Install: curl -fsSL https://claude.ai/install.sh | bash${NC}"
  exit 1
fi

if [ ! -f "$PROMPT_FILE" ]; then
  echo -e "${RED}Error: $PROMPT_FILE not found. Create it first.${NC}"
  exit 1
fi

if [ ! -d ".git" ]; then
  echo -e "${RED}Error: Not a git repository.${NC}"
  exit 1
fi

# ─── Initialize activity log ────────────────────────────────────────
if [ ! -f "$ACTIVITY_LOG" ]; then
  cat > "$ACTIVITY_LOG" << 'EOF'
# Ralph Wiggum Loop — Activity Log

Autonomous iteration log. Each entry = one fresh Claude context window.

---
EOF
  log "${GREEN}Created $ACTIVITY_LOG${NC}"
fi

# ─── Show startup info ──────────────────────────────────────────────
banner "🔄 Ralph Wiggum Loop — Team Asha Chatbot"

echo -e "  ${BOLD}Max iterations:${NC}  $MAX_ITERATIONS"
echo -e "  ${BOLD}Prompt file:${NC}     $PROMPT_FILE"
echo -e "  ${BOLD}Activity log:${NC}    $ACTIVITY_LOG"
echo -e "  ${BOLD}Cooldown:${NC}        ${COOLDOWN}s between iterations"
echo -e "  ${BOLD}Branch:${NC}          $(git branch --show-current)"
echo -e "  ${BOLD}Commit:${NC}          $(git log --oneline -1)"
echo ""

if [ "$DRY_RUN" = "--dry" ]; then
  echo -e "${YELLOW}DRY RUN — would execute $MAX_ITERATIONS iterations${NC}"
  echo -e "${DIM}Prompt content:${NC}"
  head -20 "$PROMPT_FILE"
  echo -e "${DIM}...${NC}"
  exit 0
fi

# Confirm before starting
echo -e "${YELLOW}Starting in 3 seconds... (Ctrl+C to cancel)${NC}"
sleep 3

# ─── Main loop ──────────────────────────────────────────────────────
ITERATION=0
SUCCESSES=0
FAILURES=0
START_TIME=$(date +%s)

while [ $ITERATION -lt $MAX_ITERATIONS ]; do
  ITERATION=$((ITERATION + 1))
  ITER_START=$(date +%s)

  banner "Iteration $ITERATION / $MAX_ITERATIONS"

  # Show git state before
  log "${BLUE}Git status before:${NC}"
  git status --short 2>/dev/null || true
  divider

  # Log iteration start to activity
  echo "" >> "$ACTIVITY_LOG"
  echo "## Iteration $ITERATION — $(timestamp)" >> "$ACTIVITY_LOG"
  echo "" >> "$ACTIVITY_LOG"

  # Build the prompt with current state context
  PROMPT=$(cat "$PROMPT_FILE")

  # Add current git state to prompt
  PROMPT="$PROMPT

---
## Current State (auto-injected by ralph.sh)
- **Iteration:** $ITERATION of $MAX_ITERATIONS
- **Branch:** $(git branch --show-current)
- **Last commit:** $(git log --oneline -1)
- **Timestamp:** $(timestamp)
- **Previous iterations:** $((ITERATION - 1)) completed ($SUCCESSES succeeded, $FAILURES failed)
"

  # Run Claude with streaming output
  log "${GREEN}Launching Claude (iteration $ITERATION)...${NC}"
  divider

  CLAUDE_EXIT=0
  claude --print \
    --verbose \
    --dangerously-skip-permissions \
    --output-format stream-json \
    -p "$PROMPT" 2>&1 | while IFS= read -r line; do
      # Parse stream-json output for verbose display
      if echo "$line" | python3 -c "
import sys, json
try:
    obj = json.load(sys.stdin)
    t = obj.get('type', '')
    if t == 'assistant':
        msg = obj.get('message', {})
        content = msg.get('content', [])
        for block in content:
            if block.get('type') == 'text':
                print(block.get('text', ''), end='')
            elif block.get('type') == 'tool_use':
                name = block.get('name', '')
                inp = json.dumps(block.get('input', {}))[:200]
                print(f'\n\033[2m[tool: {name}] {inp}\033[0m', end='')
    elif t == 'result':
        cost = obj.get('cost_usd', 0)
        tokens_in = obj.get('input_tokens', 0)
        tokens_out = obj.get('output_tokens', 0)
        duration = obj.get('duration_ms', 0)
        print(f'\n\n\033[0;36m--- Result ---\033[0m')
        print(f'Cost: \${cost:.4f} | Tokens: {tokens_in} in / {tokens_out} out | Duration: {duration/1000:.1f}s')
except (json.JSONDecodeError, KeyError):
    pass
" 2>/dev/null; then
        true
      fi
    done || CLAUDE_EXIT=$?

  divider
  ITER_END=$(date +%s)
  ITER_DURATION=$((ITER_END - ITER_START))

  # Check result
  if [ $CLAUDE_EXIT -eq 0 ]; then
    SUCCESSES=$((SUCCESSES + 1))
    log "${GREEN}Iteration $ITERATION completed successfully (${ITER_DURATION}s)${NC}"
    echo "**Status:** Success (${ITER_DURATION}s)" >> "$ACTIVITY_LOG"
  else
    FAILURES=$((FAILURES + 1))
    log "${RED}Iteration $ITERATION failed with exit code $CLAUDE_EXIT (${ITER_DURATION}s)${NC}"
    echo "**Status:** Failed (exit $CLAUDE_EXIT, ${ITER_DURATION}s)" >> "$ACTIVITY_LOG"
  fi

  # Show git changes after
  log "${BLUE}Git changes after iteration:${NC}"
  git diff --stat HEAD~1 2>/dev/null || git status --short
  divider

  # Show commit log
  log "${BLUE}Recent commits:${NC}"
  git log --oneline -3
  divider

  # Progress summary
  ELAPSED=$((ITER_END - START_TIME))
  REMAINING=$((MAX_ITERATIONS - ITERATION))
  if [ $ITERATION -gt 0 ]; then
    AVG=$((ELAPSED / ITERATION))
    ETA=$((AVG * REMAINING))
    log "${CYAN}Progress: $ITERATION/$MAX_ITERATIONS | Success: $SUCCESSES | Failed: $FAILURES | ETA: ~$((ETA / 60))m${NC}"
  fi

  # Cooldown between iterations (skip after last)
  if [ $ITERATION -lt $MAX_ITERATIONS ]; then
    log "${DIM}Cooling down ${COOLDOWN}s before next iteration...${NC}"
    sleep $COOLDOWN
  fi
done

# ─── Summary ────────────────────────────────────────────────────────
END_TIME=$(date +%s)
TOTAL_TIME=$((END_TIME - START_TIME))

banner "Loop Complete"

echo -e "  ${BOLD}Iterations:${NC}  $MAX_ITERATIONS"
echo -e "  ${BOLD}Succeeded:${NC}   ${GREEN}$SUCCESSES${NC}"
echo -e "  ${BOLD}Failed:${NC}      ${RED}$FAILURES${NC}"
echo -e "  ${BOLD}Total time:${NC}  $((TOTAL_TIME / 60))m $((TOTAL_TIME % 60))s"
echo -e "  ${BOLD}Branch:${NC}      $(git branch --show-current)"
echo -e "  ${BOLD}Commits:${NC}"
git log --oneline -$MAX_ITERATIONS
echo ""

# Final log entry
echo "" >> "$ACTIVITY_LOG"
echo "---" >> "$ACTIVITY_LOG"
echo "## Loop Summary — $(timestamp)" >> "$ACTIVITY_LOG"
echo "- Iterations: $MAX_ITERATIONS ($SUCCESSES succeeded, $FAILURES failed)" >> "$ACTIVITY_LOG"
echo "- Total time: $((TOTAL_TIME / 60))m $((TOTAL_TIME % 60))s" >> "$ACTIVITY_LOG"
echo "" >> "$ACTIVITY_LOG"
