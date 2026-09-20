"""Run the repository's Python quality checks using the selected interpreter."""

from pathlib import Path
import subprocess
import sys


def main():
  """Check formatting, lint, and types without modifying source files."""
  root = Path(__file__).resolve().parents[1]
  paths = ['pkg/src', 'pkg/tests']
  for arguments in (
    ['ruff', 'check', *paths],
    ['ruff', 'format', '--check', *paths],
    ['pyright', '--pythonpath', sys.executable],
  ):
    command = [sys.executable, '-m', *arguments]
    print(' '.join(command), flush=True)
    result = subprocess.run(command, cwd=root)
    if result.returncode:
      return result.returncode
  return 0


if __name__ == '__main__':
  sys.exit(main())
