# Agent notes for `accounting`

1. Read `.agents/rules/python.md` before writing Python. It is a hand-synced copy of the workspace canonical file; edit the canonical file, not this copy.
2. Repo-specific rules go in `.agents/rules/`, skills in `.agents/skills/`.
3. Before reporting, run `scripts/check.py` and `python -m pytest pkg/tests -q` with the shared interpreter `../platform/.venv/bin/python` (where the repo has a `pkg/`).
4. Never add `Co-Authored-By` or other attribution trailers to commit messages.
5. There are no specifications. The code is the source of truth; docs/ explains how and why, and the docs site generated from the code shows it. Do not cite rule numbers in code or docs: say what the code does and why.
