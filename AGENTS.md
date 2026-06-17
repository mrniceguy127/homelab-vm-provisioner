# Homelab VM Provisioner Monorepo

Integrated monorepo for VM provisioning: Python CLI + Node.js API + React Client

## Quick Start

```bash
./setup      # Initialize all projects
./start      # Start dev servers (API + Client)
./build      # Build all with coverage + docs
./test-all   # Run all tests with coverage report
```

## Projects

| Project | Type | Testing |
|---------|------|---------|
| **homelab-vm-provisioner** | Python CLI | unittest |
| **homelab-vm-provisioner-api** | Express API | vitest + supertest |
| **homelab-vm-provisioner-client** | React + Vite | vitest + Playwright |

## Architecture

```
React Client → Express API → Python CLI → libvirt
```

**Component Roles**:
- **Python CLI**: Core provisioning, VM lifecycle, nftables
- **Node.js API**: HTTP layer, privilege management, config store
- **React Client**: User interface, Material-UI

## Code Style Essentials

**JavaScript**: ES modules, vitest, async/await, no defaults  
**React**: Material-UI, ThemeProvider required, Playwright for E2E  
**Python**: 3.9+, unittest (NOT pytest), ruff, Google-style docstrings

## Instruction Priority

When working inside a subproject, prefer that subproject's `AGENTS.md` for project-specific commands, framework rules, and testing patterns.

Do not assume patterns from one subproject apply to another. For example, Python uses `unittest`, the API uses `vitest`, and the client uses React testing patterns.

## AI Agents

Each project has OpenCode agents in its `.opencode/agents/` directory.

See each project's AGENTS.md for usage instructions and available agents.

## Testing Philosophy

1. **TDD**: Write tests first
2. **Coverage**: 80% minimum (enforced in API & Python)
3. **Integration**: Test full stack for user-facing features
4. **E2E**: Playwright for critical workflows (Client)

## Common Gotchas

**Python**: unittest not pytest, mock libvirt, 80% enforced  
**Node.js**: Use npm scripts not node binary, vitest context differs  
**React**: ThemeProvider required, Playwright needs dev server running

## Documentation Sources

Do not duplicate generated API, CLI, or component documentation in `AGENTS.md`.

Use the repo's actual documentation sources and build configuration. Prefer source doc comments, RST/Markdown docs, and generated documentation outputs where present.

When changing public behavior:
- Locate the relevant source docs/comments for that subproject.
- Update the docs source, not just generated output.
- Run the subproject's docs build command if one exists.
- Do not duplicate full generated documentation in `AGENTS.md`.
