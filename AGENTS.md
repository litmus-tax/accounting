# Agent notes for `accounting`

1. Read `.agents/rules/python.md` before writing Python. It is a hand-synced copy of the workspace canonical file; edit the canonical file, not this copy.
2. Repo-specific rules go in `.agents/rules/`, skills in `.agents/skills/`.
3. Before reporting, run `scripts/check.py` and `python -m pytest pkg/tests -q` with the shared interpreter `../platform/.venv/bin/python` (where the repo has a `pkg/`).
4. Never add `Co-Authored-By` or other attribution trailers to commit messages.
