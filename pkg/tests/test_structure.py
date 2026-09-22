"""The structure checker enforces a synthetic structure.toml against a temporary tree."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/structure.py'

CONFIG = """\
package = "litmus.cex"
src = "cex"
max_lines = 400

[layers]
domain = ["domain"]
parsing = ["domain", "parsing"]

[modules]
"domain/ids.py" = { doc = "Ids." }
"domain/big.py" = { doc = "Big.", split_pending = 7 }
"parsing/load.py" = { doc = "Load." }
"""

TABLES = """\
### domain

| Module | Responsibility | May import |
| --- | --- | --- |
| `domain/big.py` | Big. (split pending: #7) | `domain` |
| `domain/ids.py` | Ids. | `domain` |

### parsing

| Module | Responsibility | May import |
| --- | --- | --- |
| `parsing/load.py` | Load. | `domain`, `parsing` |
"""


@pytest.fixture
def structure() -> ModuleType:
  """Import the stdlib-only checker script from `scripts/`."""
  spec = importlib.util.spec_from_file_location('structure', SCRIPT)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


@pytest.fixture
def tree(structure: ModuleType, tmp_path: Path) -> Path:
  """A config plus the tree and rendered document it describes, with every check passing."""
  config = tmp_path / 'structure.toml'
  config.write_text(CONFIG)
  src = tmp_path / 'cex'
  for relative, text in {
    '__init__.py': '"""Root."""\n',
    'domain/__init__.py': '',
    'domain/ids.py': 'from litmus.cex.domain import big\nfrom . import big as other\n',
    'domain/big.py': '"""Big."""\n' + 'x = 1\n' * 500,
    'parsing/__init__.py': '',
    'parsing/load.py': 'import litmus.cex.domain.ids\nfrom ..domain import ids\nfrom litmus.cex import parsing\n',
  }.items():
    path = src / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
  (tmp_path / 'docs').mkdir()
  (tmp_path / 'docs/architecture.md').write_text('# Architecture\n\nIntro.\n')
  assert structure.render(config) == 0
  return config


def test_all_good(structure: ModuleType, tree: Path):
  """A tree matching its map, with relative imports resolved, has no findings."""
  assert structure.check(tree) == []
  assert structure.main(['--config', str(tree)]) == 0


def test_render(structure: ModuleType, tree: Path):
  """Rendering creates the section once and rewrites only the block between the markers."""
  doc = tree.parent / 'docs/architecture.md'
  text = doc.read_text()
  assert text.startswith('# Architecture\n\nIntro.\n\n## Module map\n\n')
  assert text.endswith(f'<!-- structure:begin -->\n{TABLES}<!-- structure:end -->\n')
  assert text.count('## Module map') == 1
  edited = text.replace('Intro.', 'Edited intro.')
  doc.write_text(edited)
  assert structure.render(tree) == 0
  assert doc.read_text() == edited


def test_stale(structure: ModuleType, tree: Path):
  """A rendered section that no longer matches the config fails until re-rendered."""
  tree.write_text(CONFIG.replace('doc = "Ids."', 'doc = "Identifiers."'))
  findings = structure.check(tree)
  assert findings == [
    f'{tree.parent / "docs/architecture.md"}: module map is stale; run scripts/structure.py --render'
  ]
  assert structure.render(tree) == 0
  assert structure.check(tree) == []


def test_unlisted(
  structure: ModuleType, tree: Path, capsys: pytest.CaptureFixture[str]
):
  """A module without an entry fails, and `--print-unlisted` drafts its entry."""
  src = tree.parent / 'cex'
  (src / 'parsing/extra.py').write_text('from litmus.cex.domain import ids\n')
  findings = structure.check(tree)
  assert findings == [f'{src / "parsing/extra.py"}: not listed in structure.toml']
  assert structure.main(['--config', str(tree), '--print-unlisted']) == 0
  assert (
    capsys.readouterr().out
    == '"parsing/extra.py" = { doc = "TODO" }  # imports: domain\n'
  )


def test_missing(structure: ModuleType, tree: Path):
  """An entry naming a module that does not exist fails."""
  (tree.parent / 'cex/parsing/load.py').unlink()
  findings = structure.check(tree)
  assert len(findings) == 1
  assert findings[0].startswith('structure.toml: `parsing/load.py` listed but missing')


def test_oversize(structure: ModuleType, tree: Path):
  """Over the limit fails unless the entry carries `split_pending`."""
  tree.write_text(CONFIG.replace(', split_pending = 7', ''))
  assert structure.render(tree) == 0
  findings = structure.check(tree)
  assert len(findings) == 1
  assert findings[0].startswith(
    f'{tree.parent / "cex/domain/big.py"}: 501 lines exceed 400'
  )


def test_bad_import(structure: ModuleType, tree: Path):
  """An import outside the layer's allowance fails, absolute or relative, unless excused."""
  ids = tree.parent / 'cex/domain/ids.py'
  ids.write_text('import litmus.cex.parsing.load\nfrom ..parsing import load\n')
  findings = structure.check(tree)
  assert len(findings) == 2
  assert findings[0].startswith(f'{ids}:1: imports `litmus.cex.parsing.load`')
  assert findings[1].startswith(f'{ids}:2: imports `litmus.cex.parsing`')
  assert structure.main(['--config', str(tree)]) == 1
  tree.write_text(CONFIG.replace('{ doc = "Ids." }', '{ doc = "Ids.", layering = 9 }'))
  assert structure.render(tree) == 0
  assert structure.check(tree) == []
  assert (
    '| `domain/ids.py` | Ids. | `domain` (layering: #9) |'
    in (tree.parent / 'docs/architecture.md').read_text()
  )


def test_undeclared_layer(structure: ModuleType, tree: Path):
  """A module in a layer the config does not declare is a finding."""
  tree.write_text(CONFIG.replace('parsing = ["domain", "parsing"]\n', ''))
  assert structure.render(tree) == 0
  assert structure.check(tree) == [
    'structure.toml: `parsing/load.py` belongs to undeclared layer `parsing`'
  ]


def test_invalid_config(structure: ModuleType, tree: Path):
  """Bad entries and undeclared allowances are reported before anything else."""
  tree.write_text(
    CONFIG.replace('domain = ["domain"]', 'domain = ["domain", "storage"]').replace(
      '{ doc = "Load." }', '{ doc = "Load.", extra = 1 }'
    )
  )
  assert structure.check(tree) == [
    'structure.toml: layer `domain` allows undeclared layer `storage`',
    "structure.toml: `parsing/load.py` has unknown keys ['extra']",
  ]
  tree.write_text('package = 1\n')
  findings = structure.check(tree)
  assert findings[0] == 'structure.toml: `package` must be a non-empty string'


def test_missing_section(structure: ModuleType, tree: Path):
  """A document without the rendered section is a finding, not a pass."""
  doc = tree.parent / 'docs/architecture.md'
  doc.write_text('# Architecture\n')
  assert structure.check(tree) == [
    f'{doc}: no module map section; run scripts/structure.py --render'
  ]


def test_root_symbol(structure: ModuleType, tree: Path):
  """`from package import name` targets the root `__init__` when `name` is not a module."""
  ids = tree.parent / 'cex/domain/ids.py'
  (tree.parent / 'cex/__init__.py').write_text('"""Root."""\n__version__ = "1"\n')
  ids.write_text('from litmus.cex import __version__\n')
  config = CONFIG.replace(
    '[modules]\n', '[modules]\n"__init__.py" = { doc = "Version." }\n'
  )
  tree.write_text(config)
  assert structure.render(tree) == 0
  findings = structure.check(tree)
  assert len(findings) == 2
  assert (
    findings[0]
    == 'structure.toml: `__init__.py` belongs to undeclared layer `__init__`'
  )
  assert findings[1].startswith(f'{ids}:1: imports `litmus.cex.__init__`')
  tree.write_text(
    config.replace('[layers]\n', '[layers]\n__init__ = []\n').replace(
      'domain = ["domain"]', 'domain = ["domain", "__init__"]'
    )
  )
  assert structure.render(tree) == 0
  assert structure.check(tree) == []
