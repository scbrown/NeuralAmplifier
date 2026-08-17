# Beads - AI-Native Issue Tracking

Welcome to Beads! This repository uses **Beads** for issue tracking - a modern, AI-native tool designed to live directly in your codebase alongside your code.

## What is Beads?

Beads is issue tracking that lives in your repo, making it perfect for AI coding agents and developers who want their issues close to their code. No web UI required - everything works through the CLI and integrates seamlessly with git.

**Learn more:** [github.com/steveyegge/beads](https://github.com/steveyegge/beads)

## Quick Start

### Essential Commands

```bash
# Create new issues
bd create "Add user authentication"

# View all issues
bd list

# View issue details
bd show <issue-id>

# Update issue status
bd update <issue-id> --claim
bd update <issue-id> --status done

# Sync with Dolt remote
bd dolt push
```

### Working with Issues

Issues in Beads are:
- **Git-native**: Stored in Dolt database with version control and branching
- **AI-friendly**: CLI-first design works perfectly with AI coding agents
- **Branch-aware**: Issues can follow your branch workflow
- **Sync-ready**: Uses Dolt remotes for backup and team sharing

## Why Beads?

✨ **AI-Native Design**
- Built specifically for AI-assisted development workflows
- CLI-first interface works seamlessly with AI coding agents
- No context switching to web UIs

🚀 **Developer Focused**
- Issues live in your repo, right next to your code
- Works offline, syncs when you push
- Fast, lightweight, and stays out of your way

🔧 **Git Integration**
- Dolt-native sync via bd dolt push / bd dolt pull
- Branch-aware issue tracking
- Dolt-native three-way merge resolution

## Get Started with Beads

Try Beads in your own projects:

```bash
# Install Beads
curl -sSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash

# Initialize in your repo
bd init

# Create your first issue
bd create "Try out Beads"
```

## Two warnings this repo has met, and what to do about them

**`.beads has permissions 0755 (recommended: 0700)`** — real, and now handled by
`scripts/setup-environment.sh`. The directory holds the Dolt issue database, which at 0755 is
readable by anyone on a shared machine. Git does not track directory permissions, so a `chmod`
in one checkout helps nobody else; the setup script is the only place a fix propagates. It fired
on roughly forty commands in a single session before anyone acted on it, which is the argument
for fixing it rather than for reading it more carefully.

**`beads.role not configured (GH#2950)`** — surfaced only once the louder one stopped, which is
the more interesting half of the story: a noisy warning had been masking a quieter one for the
whole session.

It is **deliberately still unset**. `bd` offers `maintainer` or `contributor`, neither is
documented in the local tooling or `bd help`, and the name suggests it governs who may write —
so guessing is a claim about authority, not a configuration tidy-up. `git config beads.role
<role>` when someone who knows the repo's intent can say which. The
[Agent Context Profiles](../CLAUDE.md) section is the closest thing to an answer and is about
agent behaviour rather than this setting.

## Learn More

- **Documentation**: [github.com/steveyegge/beads/docs](https://github.com/steveyegge/beads/tree/main/docs)
- **Quick Start Guide**: Run `bd quickstart`
- **Examples**: [github.com/steveyegge/beads/examples](https://github.com/steveyegge/beads/tree/main/examples)

---

*Beads: Issue tracking that moves at the speed of thought* ⚡
