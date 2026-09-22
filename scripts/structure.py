"""Enforce the module map in structure.toml and render it into docs/architecture.md.

`structure.toml` at the repository root names the package (`package`), its source
directory (`src`, relative to the file), the line limit (`max_lines`), the layers
(`[layers]`: for each top-level package, or root module named by its stem, the
layers it may import) and one entry per module (`[modules]`: `doc`, plus
`layering = N` when issue N tracks its import violations and `split_pending = N`
when issue N tracks splitting it past the limit). This script fails when a module
is unlisted or missing, when its layer is undeclared, when it exceeds the limit
without `split_pending`, when it imports a layer outside its allowance without
`layering`, or when the tables rendered into `docs/architecture.md` are stale.
`--print-unlisted` prints entries to paste for unknown modules; `--render` rewrites
the tables between the `structure:begin` and `structure:end` markers.
"""

import argparse
import ast
from pathlib import Path
import sys
import tomllib

BEGIN = '<!-- structure:begin -->'
END = '<!-- structure:end -->'
HEADING = '## Module map'
COLUMNS = ('Module', 'Responsibility', 'May import')
ENTRY_KEYS = {'doc', 'layering', 'split_pending'}
INTRO = (
  '`scripts/check.py` runs `scripts/structure.py` first. `structure.toml` is the '
  'source of truth: it declares the layers (top-level packages, or root modules named '
  'by their stem), the layers each may import, and one entry per module with its '
  'responsibility. The check fails when a module under `{src}` has no entry, when an '
  'entry names a module that no longer exists, when a module exceeds {max_lines} lines '
  'without `split_pending = N`, when a module imports a layer outside its allowance '
  'without `layering = N` (issue N tracks the violation; remove the import and the '
  'marker together), or when the tables below are stale. Paths are relative to '
  '`{src}/`; an `__init__.py` holding nothing but a docstring needs no entry. '
  '`python scripts/structure.py --print-unlisted` drafts entries for unknown modules '
  'and `python scripts/structure.py --render` regenerates the tables below. Edit '
  '`structure.toml`, not the tables.'
)


class Entry:
  """One module entry: its responsibility and the issues excusing its findings."""

  def __init__(self, doc: str, layering: int | None, split_pending: int | None):
    self.doc = doc
    self.layering = layering
    self.split_pending = split_pending


class Config:
  """The parsed `structure.toml`: package, source root, limit, layers and entries."""

  def __init__(self, path: Path, data: dict[str, object]):
    self.path = path
    self.errors: list[str] = []
    self.package = self.string(data, 'package')
    self.src = path.parent / self.string(data, 'src')
    self.doc = path.parent / 'docs/architecture.md'
    max_lines = data.get('max_lines', 400)
    if not isinstance(max_lines, int):
      self.errors.append(f'{path.name}: `max_lines` must be an integer')
      max_lines = 400
    self.max_lines = max_lines
    self.layers = self.parse_layers(data.get('layers', {}))
    self.entries = self.parse_modules(data.get('modules', {}))

  def string(self, data: dict[str, object], key: str) -> str:
    """A required top-level string value, recorded as an error when absent."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
      self.errors.append(f'{self.path.name}: `{key}` must be a non-empty string')
      return ''
    return value

  def parse_layers(self, data: object) -> dict[str, list[str]]:
    """Read `[layers]` as layer name to the list of layers it may import."""
    layers: dict[str, list[str]] = {}
    if not isinstance(data, dict):
      self.errors.append(f'{self.path.name}: `[layers]` must be a table')
      return layers
    for name, allowed in data.items():
      if not isinstance(allowed, list) or not all(isinstance(a, str) for a in allowed):
        self.errors.append(f'{self.path.name}: layer `{name}` must list layer names')
        continue
      layers[name] = list(allowed)
    for name, allowed in layers.items():
      for other in allowed:
        if other not in layers:
          self.errors.append(
            f'{self.path.name}: layer `{name}` allows undeclared layer `{other}`'
          )
    return layers

  def parse_modules(self, data: object) -> dict[str, Entry]:
    """Read `[modules]` as module path to its entry, validating each inline table."""
    entries: dict[str, Entry] = {}
    if not isinstance(data, dict):
      self.errors.append(f'{self.path.name}: `[modules]` must be a table')
      return entries
    for module, value in data.items():
      if not module.endswith('.py') or module.startswith('/') or '\\' in module:
        self.errors.append(f'{self.path.name}: `{module}` is not a relative `.py` path')
        continue
      if not isinstance(value, dict):
        self.errors.append(f'{self.path.name}: `{module}` must be an inline table')
        continue
      unknown = set(value) - ENTRY_KEYS
      if unknown:
        self.errors.append(
          f'{self.path.name}: `{module}` has unknown keys {sorted(unknown)}'
        )
      doc = value.get('doc')
      if not isinstance(doc, str) or not doc:
        self.errors.append(f'{self.path.name}: `{module}` needs a non-empty `doc`')
        doc = ''
      issues: dict[str, int | None] = {}
      for key in ('layering', 'split_pending'):
        issue = value.get(key)
        if issue is not None and not isinstance(issue, int):
          self.errors.append(
            f'{self.path.name}: `{module}` `{key}` must be an issue number'
          )
          issue = None
        issues[key] = issue
      entries[module] = Entry(doc, issues['layering'], issues['split_pending'])
    return entries


def load(path: Path) -> Config:
  """Parse `structure.toml`; parse errors become the config's only finding."""
  try:
    data = tomllib.loads(path.read_text())
  except (OSError, tomllib.TOMLDecodeError) as error:
    config = Config(path, {'package': 'x', 'src': '.'})
    config.errors = [f'{path.name}: {error}']
    return config
  return Config(path, data)


def is_empty_init(path: Path, tree: ast.Module) -> bool:
  """An `__init__.py` with no statements beyond a docstring needs no entry."""
  if path.name != '__init__.py':
    return False
  body = tree.body
  if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
    body = body[1:]
  return not body


def layer_of(relative: str) -> str:
  """The layer a module path belongs to: its first directory, or its stem at the root."""
  first = relative.split('/')[0]
  return first[: -len('.py')] if first.endswith('.py') else first


def top_level(module: str, package: str) -> str:
  """The layer an absolute imported module belongs to: its first component after the package."""
  rest = module[len(package) :].lstrip('.')
  return rest.split('.')[0]


def resolve_relative(relative: str, level: int, base: str | None) -> str | None:
  """Turn a relative import inside `relative` (the importing module) into an absolute module."""
  parts = relative.split('.')
  if level > len(parts):
    return None
  anchor = parts[: len(parts) - level + 1] if level else parts
  return '.'.join(anchor + ([base] if base else []))


def root_member(src: Path, package: str, name: str) -> str:
  """`from package import name` names a submodule when one exists, else the root `__init__`."""
  if (src / f'{name}.py').is_file() or (src / name).is_dir():
    return f'{package}.{name}'
  return f'{package}.__init__'


def imported_modules(
  path: Path, tree: ast.Module, module: str, package: str, src: Path
) -> list[tuple[int, str]]:
  """Every module of the package the file imports, absolute, with its line number."""
  found: list[tuple[int, str]] = []
  parent = module if path.name == '__init__.py' else module.rsplit('.', 1)[0]
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      for alias in node.names:
        if alias.name == package or alias.name.startswith(package + '.'):
          found.append((node.lineno, alias.name))
    elif isinstance(node, ast.ImportFrom):
      if node.level:
        target = resolve_relative(parent, node.level, node.module)
        if target is None:
          continue
      else:
        target = node.module or ''
      if target != package and not target.startswith(package + '.'):
        continue
      if target == package:
        found.extend(
          (node.lineno, root_member(src, package, a.name)) for a in node.names
        )
      else:
        found.append((node.lineno, target))
  return found


def source_modules(src: Path) -> dict[str, tuple[Path, ast.Module]]:
  """Every `.py` under the package root that the map must list, parsed."""
  modules: dict[str, tuple[Path, ast.Module]] = {}
  for path in sorted(src.rglob('*.py')):
    if '__pycache__' in path.parts:
      continue
    tree = ast.parse(path.read_text(), filename=str(path))
    if is_empty_init(path, tree):
      continue
    modules[path.relative_to(src).as_posix()] = (path, tree)
  return modules


def module_name(relative: str, package: str) -> str:
  """Absolute module name for a path relative to the package root."""
  parts = relative[: -len('.py')].split('/')
  if parts[-1] == '__init__':
    parts = parts[:-1]
  return '.'.join([package, *parts])


def cell(text: str) -> str:
  """Escape a value for a Markdown table cell."""
  return text.replace('|', '\\|')


def render_tables(config: Config) -> str:
  """The per-layer tables for every entry, grouped in layer declaration order."""
  groups: dict[str, list[str]] = {}
  for module in sorted(config.entries):
    group = 'Root' if '/' not in module else layer_of(module)
    groups.setdefault(group, []).append(module)
  order = ['Root'] + [name for name in config.layers if name in groups]
  order += [name for name in groups if name not in order]
  parts: list[str] = []
  for group in order:
    if group not in groups:
      continue
    lines = [f'### {group}', '', f'| {" | ".join(COLUMNS)} |', '| --- | --- | --- |']
    for module in groups[group]:
      entry = config.entries[module]
      doc = cell(entry.doc)
      if entry.split_pending is not None:
        doc += f' (split pending: #{entry.split_pending})'
      allowed = ', '.join(
        f'`{name}`' for name in config.layers.get(layer_of(module), [])
      )
      allowed = allowed or '—'
      if entry.layering is not None:
        allowed += f' (layering: #{entry.layering})'
      lines.append(f'| `{module}` | {doc} | {allowed} |')
    parts.append('\n'.join(lines) + '\n')
  return '\n'.join(parts)


def section(text: str) -> tuple[int, int] | None:
  """Character offsets of the rendered block between the markers, or none."""
  begin = text.find(BEGIN)
  end = text.find(END, begin)
  if begin < 0 or end < 0:
    return None
  return begin + len(BEGIN), end


def check(config_path: Path) -> list[str]:
  """Return one message per finding; an empty list means the tree matches the map."""
  config = load(config_path)
  findings = list(config.errors)
  if findings:
    return findings
  modules = source_modules(config.src)
  entries = config.entries
  for relative in sorted(set(modules) - set(entries)):
    findings.append(f'{config.src / relative}: not listed in {config_path.name}')
  for relative in sorted(set(entries) - set(modules)):
    findings.append(
      f'{config_path.name}: `{relative}` listed but missing from {config.src}'
    )
  for relative, (path, tree) in modules.items():
    entry = entries.get(relative)
    count = len(path.read_text().splitlines())
    if count > config.max_lines and not (entry and entry.split_pending is not None):
      findings.append(
        f'{path}: {count} lines exceed {config.max_lines}; split it or mark the entry `split_pending = N`'
      )
    if entry is None:
      continue
    layer = layer_of(relative)
    if layer not in config.layers:
      findings.append(
        f'{config_path.name}: `{relative}` belongs to undeclared layer `{layer}`'
      )
      continue
    if entry.layering is not None:
      continue
    allowed = config.layers[layer]
    own = module_name(relative, config.package)
    for lineno, target in imported_modules(path, tree, own, config.package, config.src):
      imported = top_level(target, config.package)
      if imported and imported not in allowed:
        findings.append(
          f'{path}:{lineno}: imports `{target}` but layer `{layer}` allows only {allowed or "nothing"}'
        )
  doc = config.doc.read_text() if config.doc.exists() else ''
  span = section(doc)
  if span is None:
    findings.append(
      f'{config.doc}: no module map section; run scripts/structure.py --render'
    )
  elif doc[span[0] : span[1]] != '\n' + render_tables(config):
    findings.append(
      f'{config.doc}: module map is stale; run scripts/structure.py --render'
    )
  return findings


def render(config_path: Path) -> int:
  """Write the tables into the architecture document, creating the section if absent."""
  config = load(config_path)
  for error in config.errors:
    print(error)
  if config.errors:
    return 1
  doc = config.doc.read_text() if config.doc.exists() else '# Architecture\n'
  span = section(doc)
  tables = '\n' + render_tables(config)
  if span is None:
    intro = INTRO.format(
      src=config.src.relative_to(config_path.parent).as_posix(),
      max_lines=config.max_lines,
    )
    doc = doc.rstrip('\n') + f'\n\n{HEADING}\n\n{intro}\n\n{BEGIN}{tables}{END}\n'
  else:
    doc = doc[: span[0]] + tables + doc[span[1] :]
  config.doc.parent.mkdir(parents=True, exist_ok=True)
  config.doc.write_text(doc)
  return 0


def print_unlisted(config_path: Path) -> int:
  """Emit entries for modules the map does not list, to paste into `[modules]`."""
  config = load(config_path)
  for error in config.errors:
    print(error)
  if config.errors:
    return 1
  modules = source_modules(config.src)
  for relative in sorted(set(modules) - set(config.entries)):
    path, tree = modules[relative]
    own = module_name(relative, config.package)
    imports = sorted(
      {
        top_level(target, config.package)
        for _, target in imported_modules(path, tree, own, config.package, config.src)
      }
    )
    comment = f'  # imports: {", ".join(imports)}' if imports else ''
    print(f'"{relative}" = {{ doc = "TODO" }}{comment}')
  return 0


def main(argv: list[str] | None = None) -> int:
  """Check the tree against the map, render the tables, or print unlisted entries."""
  root = Path(__file__).resolve().parents[1]
  parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
  parser.add_argument('--config', type=Path, default=root / 'structure.toml')
  parser.add_argument(
    '--print-unlisted', action='store_true', help='print entries for unlisted modules'
  )
  parser.add_argument(
    '--render', action='store_true', help='write the tables into docs/architecture.md'
  )
  args = parser.parse_args(argv)
  if args.print_unlisted:
    return print_unlisted(args.config)
  if args.render:
    return render(args.config)
  findings = check(args.config)
  for finding in findings:
    print(finding)
  if findings:
    print(f'{len(findings)} structure finding(s)')
    return 1
  print('structure: ok')
  return 0


if __name__ == '__main__':
  sys.exit(main())
