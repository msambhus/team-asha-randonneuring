---
created: 2026-04-28T03:23:12.067Z
title: Replace gh CLI device-flow auth with PAT for anatesan
area: tooling
files: []
---

## Problem

The `gh` CLI is currently authenticated as `ashok-natesan-strategem`, but git operations in this repo use the `github-personal` SSH key which authenticates as `anatesan` (ashok.natesan@gmail.com). This mismatch means `gh pr create` fails with "must be a collaborator" on `msambhus/team-asha-randonneuring`.

A device-flow login was done as a temporary fix, but it requires browser interaction each time the token expires.

## Solution

1. Go to https://github.com/settings/tokens (logged in as `anatesan` / ashok.natesan@gmail.com)
2. Generate a classic PAT with `repo` scope
3. Run: `gh auth login --hostname github.com --git-protocol ssh --with-token` and paste the token
4. Run: `gh auth switch --user anatesan` to make it the default

This gives a persistent token that works without browser interaction and matches the SSH key used for git push.
