"""Independent, offline maze grader. Candidate JavaScript runs only in Docker.

See README.md in this directory for the 100-point rubric and measurement limits.
"""
from __future__ import annotations

import copy
import heapq
import json
import math
import random
import re
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from codex_bench.grading import GradeContext, TestResult, aggregate
from codex_bench.util import BenchError, regular_files, write_json

DIRECTIONS = ((0, -1, 1, 4), (1, 0, 2, 8), (0, 1, 4, 1), (-1, 0, 8, 2))
ALGORITHMS = ('bfs', 'dijkstra', 'astar')
NODE_IMAGE = 'node:22-bookworm-slim'
NODE_BUILTINS = {'assert', 'assert/strict', 'test', 'fs', 'fs/promises', 'path', 'url', 'util', 'vm',
                 'perf_hooks', 'crypto', 'os', 'events', 'buffer', 'stream', 'timers', 'process',
                 'child_process', 'readline', 'http', 'https', 'zlib', 'module', 'worker_threads'}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def graph(maze, width=None, height=None):
    """Validate the specified representation and independently build adjacency."""
    require(isinstance(maze, dict), 'maze must be an object')
    w, h, cells = maze.get('width'), maze.get('height'), maze.get('cells')
    require(integer(w) and integer(h) and w > 0 and h > 0, 'width/height must be positive integers')
    require(w * h <= 100000, 'grader graph budget exceeded')
    require(width is None or w == width, 'returned width differs from requested width')
    require(height is None or h == height, 'returned height differs from requested height')
    require(isinstance(cells, list) and len(cells) == w * h, 'cells must contain width*height entries')
    for i, cell in enumerate(cells):
        require(isinstance(cell, dict), f'cell {i} must be an object')
        require(integer(cell.get('walls')) and 0 <= cell['walls'] <= 15, f'cell {i}: walls must be integer 0..15')
        require(integer(cell.get('cost')) and 1 <= cell['cost'] <= 9, f'cell {i}: cost must be integer 1..9')
    adjacency = [[] for _ in cells]
    for i, cell in enumerate(cells):
        x, y = i % w, i // w
        for dx, dy, wall, reverse in DIRECTIONS:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h):
                require(cell['walls'] & wall, f'cell {i}: open outer boundary')
                continue
            j = ny * w + nx
            require(bool(cell['walls'] & wall) == bool(cells[j]['walls'] & reverse), f'inconsistent reciprocal wall {i}<->{j}')
            if not cell['walls'] & wall:
                adjacency[i].append(j)
    return adjacency


def distances(maze, start, weighted=False):
    adjacency = graph(maze)
    result = [math.inf] * len(adjacency)
    result[start] = 0
    if weighted:
        heap = [(0, start)]
        while heap:
            distance, cell = heapq.heappop(heap)
            if distance != result[cell]:
                continue
            for neighbor in adjacency[cell]:
                candidate = distance + maze['cells'][neighbor]['cost']
                if candidate < result[neighbor]:
                    result[neighbor] = candidate
                    heapq.heappush(heap, (candidate, neighbor))
    else:
        queue = deque([start])
        while queue:
            cell = queue.popleft()
            for neighbor in adjacency[cell]:
                if math.isinf(result[neighbor]):
                    result[neighbor] = result[cell] + 1
                    queue.append(neighbor)
    return result


def maze_identity(value):
    graph(value)
    return value['width'], value['height'], [(cell['walls'], cell['cost']) for cell in value['cells']]


def solution_identity(value):
    return {key: value[key] for key in ('found', 'path', 'visitedOrder', 'cost')}


def check_solution(maze, options, value, optimal=True):
    adjacency = graph(maze)
    start, end = options['start'], options['end']
    require(isinstance(value, dict), 'solver must return an object')
    require(type(value.get('found')) is bool, 'found must be boolean')
    path = value.get('path')
    require(isinstance(path, list), 'path must be an array')
    require('cost' in value, 'cost is missing')
    expected = distances(maze, start, options['algorithm'] != 'bfs')[end]
    if math.isinf(expected):
        require(value['found'] is False and path == [] and value['cost'] is None, 'unreachable must return found=false, path=[], cost=null')
        return
    require(value['found'] is True, 'reachable target reported as unreachable')
    require(path and path[0] == start and path[-1] == end, 'path must include the exact start/end')
    require(all(integer(i) and 0 <= i < len(adjacency) for i in path), 'path contains an invalid cell')
    require(all(b in adjacency[a] for a, b in zip(path, path[1:])), 'path crosses a wall, moves diagonally, or jumps cells')
    cost = sum(maze['cells'][i]['cost'] for i in path[1:])
    require(isinstance(value['cost'], (int, float)) and not isinstance(value['cost'], bool) and math.isfinite(value['cost']), 'cost must be a finite number')
    require(value['cost'] == cost, f'cost must sum entered cells only: expected {cost}, received {value["cost"]}')
    if optimal:
        actual = len(path) - 1 if options['algorithm'] == 'bfs' else cost
        require(actual == expected, f'nonoptimal {options["algorithm"]}: expected {expected}, received {actual}')


def check_visited(maze, options, value):
    adjacency = graph(maze)
    visited = value.get('visitedOrder')
    require(isinstance(visited, list), 'visitedOrder must be an array')
    if options['start'] == options['end'] and not visited:
        return  # A solver may short-circuit before expanding the start.
    require(visited and visited[0] == options['start'], 'expansion order must begin at start')
    expanded = set()
    ordered = distances(maze, options['start'], options['algorithm'] == 'dijkstra')
    previous = -1
    for cell in visited:
        require(integer(cell) and 0 <= cell < len(adjacency), 'visitedOrder contains invalid cells')
        require(cell == options['start'] or any(n in expanded for n in adjacency[cell]), 'visitedOrder jumps to a cell outside the explored frontier')
        if options['algorithm'] in {'bfs', 'dijkstra'}:
            require(ordered[cell] >= previous, 'BFS/Dijkstra expansion distances are not nondecreasing')
            previous = ordered[cell]
        expanded.add(cell)


def make_maze(width, height, edges, costs=None):
    cells = [{'walls': 15, 'cost': 1 if costs is None else costs[i]} for i in range(width * height)]
    for a, b in edges:
        for dx, dy, wall, reverse in DIRECTIONS:
            if a % width + dx == b % width and a // width + dy == b // width:
                cells[a]['walls'] &= ~wall
                cells[b]['walls'] &= ~reverse
                break
        else:
            raise ValueError('oracle fixture contains a non-adjacent edge')
    return {'width': width, 'height': height, 'cells': cells}


def all_edges(width, height):
    return [(i, j) for i in range(width * height) for j in (i + 1, i + width)
            if j < width * height and (j == i + width or i // width == j // width)]


def search_cases():
    # Expected answers are recomputed on the host; none are sent to the candidate.
    cases = [
        ('weighted_detour', make_maze(3, 2, all_edges(3, 2), [9, 9, 1, 1, 1, 1]), 0, 2),
        ('start_not_charged', make_maze(2, 1, [(0, 1)], [9, 2]), 0, 1),
        ('reverse_cost', make_maze(2, 1, [(0, 1)], [9, 2]), 1, 0),
        ('unreachable', make_maze(3, 2, [(0, 1), (1, 4), (2, 5)]), 0, 5),
        ('same_endpoint', make_maze(3, 2, all_edges(3, 2), [2, 3, 9, 4, 5, 6]), 2, 2),
        ('single_cell', make_maze(1, 1, [], [9]), 0, 0),
        ('noncorner_endpoints', make_maze(4, 3, all_edges(4, 3), [1, 8, 1, 3, 9, 1, 2, 5, 1, 1, 9, 1]), 9, 3),
        ('all_blocked', make_maze(2, 2, []), 1, 2),
    ]
    rng = random.Random(0x4D415A45)
    for number in range(16):
        w, h = rng.randrange(3, 9), rng.randrange(3, 8)
        edges = [edge for edge in all_edges(w, h) if rng.random() < 0.68]
        costs = [rng.randrange(1, 10) for _ in range(w * h)]
        cases.append((f'random_{number}', make_maze(w, h, edges, costs), rng.randrange(w * h), rng.randrange(w * h)))
    return cases


class Entry(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.scripts = []
        self.module_scripts = False
        self.inline_scripts = False
        self.base = ''

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'base' and not self.base:
            self.base = attrs.get('href', '')
        keys = []
        if tag in {'script', 'img', 'iframe', 'source', 'audio', 'video', 'embed', 'input'}:
            keys = ['src']
        if tag in {'img', 'source'}:
            keys.append('srcset')
        if tag == 'video':
            keys.append('poster')
        if tag == 'object':
            keys = ['data']
        if tag == 'link' and set(attrs.get('rel', '').lower().split()) & {'stylesheet', 'icon', 'preload', 'modulepreload'}:
            keys = ['href']
        for key in keys:
            value = attrs.get(key)
            if value:
                self.assets.extend(part.strip().split()[0] for part in value.split(',') if part.strip()) if key == 'srcset' and not value.startswith('data:') else self.assets.append(value)
        if tag == 'script':
            self.module_scripts |= attrs.get('type', '').lower() == 'module'
            self.inline_scripts |= not attrs.get('src') and attrs.get('type', '').lower() not in {'importmap', 'application/json', 'application/ld+json'}
            if attrs.get('src'):
                self.scripts.append(attrs['src'])


def strip_comments(source):
    # A conservative lexical scan: preserve strings, discard ordinary JS/CSS comments.
    output, quote, i = [], None, 0
    while i < len(source):
        char = source[i]
        if quote:
            output.append(char)
            if char == '\\' and i + 1 < len(source):
                i += 1
                output.append(source[i])
            elif char == quote:
                quote = None
        elif char in ('"', "'", '`'):
            quote = char
            output.append(char)
        elif source[i:i + 2] == '/*':
            end = source.find('*/', i + 2)
            i = len(source) if end < 0 else end + 1
            output.append(' ')
        elif source[i:i + 2] == '//' and (i == 0 or source[i - 1] != '\\'):
            end = source.find('\n', i + 2)
            i = len(source) if end < 0 else end
            output.append('\n')
        else:
            output.append(char)
        i += 1
    return ''.join(output)


def asset_checks(submission):
    entry = Entry()
    entry.feed((submission / 'index.html').read_text(encoding='utf-8-sig'))
    references = [(submission / 'index.html', urljoin(entry.base, value)) for value in entry.assets]
    if entry.base and (urlsplit(entry.base).scheme or urlsplit(entry.base).netloc):
        references.append((submission / 'index.html', entry.base))
    files = list(regular_files(submission))
    sources = [p for p in files if p.suffix.lower() in {'.js', '.mjs', '.cjs', '.css', '.html', '.htm'}]
    if sum(p.stat().st_size for p in sources) > 8 * 1024 * 1024:
        raise BenchError('Static source scan exceeds its documented 8 MiB budget')
    for source in sources:
        content = strip_comments(source.read_text(encoding='utf-8-sig', errors='replace'))
        for match in re.finditer(r'url\(\s*[\'\"]?([^\s)\'\"]+)', content, re.I):
            references.append((source, match.group(1)))
        for match in re.finditer(r'(?:\bfrom\s*|\b(?:import|require|fetch)\s*\(\s*|\bimport\s*|@import\s*)[\'\"]([^\'\"]+)', content):
            references.append((source, match.group(1)))
    missing, remote = [], []
    for source, value in references:
        if value.startswith(('data:', '#', 'blob:', 'node:')) or value in NODE_BUILTINS:
            continue
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or value.startswith('//'):
            remote.append(value)
            continue
        # Runtime package imports are not local assets, and aren't resolved by this static check.
        if not parsed.path:
            continue
        relative = unquote(parsed.path)
        target = (source.parent / relative).resolve()
        if not target.is_relative_to(submission.resolve()):
            missing.append(value + ' (outside submission)')
        elif not any(p.is_file() for p in [target, Path(str(target) + '.js'), Path(str(target) + '.json'), target / 'index.js']):
            missing.append(value)
    return entry, sorted(set(missing)), sorted(set(remote))


def generation_plan():
    operations, groups = [], {}
    for algorithm in ('dfs', 'prim'):
        specs = []
        for index, (w, h, seed) in enumerate([(2, 2, 0), (4, 7, 1), (7, 4, 4294967295), (8, 8, 12345)]):
            specs.append((f'base_{index}', w, h, seed, 0, False))
        specs += [(f'edge_{i}', w, h, 11, 0, False) for i, (w, h) in enumerate([(1, 1), (1, 7), (7, 1)])]
        specs += [(f'loop_{i}', 7, 5, 187, ratio, False) for i, ratio in enumerate([0, 0.25, 0.5, 1])]
        specs += [(f'weight_{i}', 7, 6, seed, 0.35, True) for i, seed in enumerate([0, 99, 4294967295])]
        # Repeated calls separated by other calls catch accidental global RNG state.
        specs += [('repeat_a', 7, 6, 99, 0.35, True), ('interleave', 3, 4, 734, 0, False), ('repeat_b', 7, 6, 99, 0.35, True)]
        group = []
        for suffix, w, h, seed, loops, weighted in specs:
            operation = {'id': f'{algorithm}_{suffix}', 'kind': 'generate',
                         'options': {'width': w, 'height': h, 'algorithm': algorithm, 'seed': seed, 'loops': loops, 'weighted': weighted}}
            operations.append(operation)
            group.append(operation)
        groups[algorithm] = group
    return operations, groups


class Runtime:
    def __init__(self, submission, context):
        options = copy.deepcopy(context.options)
        options.setdefault('docker_image', NODE_IMAGE)
        isolated = GradeContext(context.artifact_dir, context.plugin_dir, context.test_timeout_seconds, options)
        self.executor = isolated.docker(submission)
        self.bridge = (context.plugin_dir / 'bridge.js').read_text(encoding='utf-8')
        self.artifacts = context.artifact_dir
        self.executions = []
        self.measurements = []

    def batch(self, name, operations, **extra):
        payload = {'operations': operations, **extra}
        response = self.executor.run(name, ['node', '-e', self.bridge], payload)
        if response['status'] == 'skipped' and response.get('exit_code') in {125, 126, 127}:
            reason = (response.get('stderr', '') + ' ' + response.get('detail', '')).lower()
            infrastructure = ('docker:', 'error response from daemon', 'failed to connect to the docker',
                              'executable file not found', 'oci runtime', 'cannot connect to the docker')
            if not any(marker in reason for marker in infrastructure):
                response = {**response, 'status': 'error', 'detail': 'Candidate Node process exited with code ' + str(response['exit_code'])}
        self.executions.append({'batch': name, 'status': response['status'], 'duration_seconds': response.get('duration_seconds'),
                                'exit_code': response.get('exit_code'), 'detail': response.get('detail', '')})
        default = {'status': response['status'], 'detail': response.get('detail', ''), 'duration_ms': None}
        try:
            data = json.loads(response.get('stdout', '')) if response['status'] == 'ok' else {}
            require(isinstance(data, dict), 'adapter response is not an object')
            results = data.get('results', [])
            require(isinstance(results, list), 'adapter results is not an array')
            indexed = {r['id']: r for r in results if isinstance(r, dict) and isinstance(r.get('id'), str)}
            self.measurements.extend({'batch': name, 'id': key, 'duration_ms': row.get('duration_ms')}
                                     for key, row in indexed.items())
            if response['status'] == 'ok' and not data.get('api'):
                default.update(status='error', detail=data.get('load_error', 'core API missing'))
            rows = {}
            for op in operations:
                result = indexed.get(op['id'])
                if result is None:
                    rows[op['id']] = {**default, 'status': 'error' if default['status'] == 'ok' else default['status'],
                                      'detail': default['detail'] or 'missing operation result'}
                else:
                    rows[op['id']] = {**result, 'status': 'ok' if result.get('ok') is True else 'error', 'detail': result.get('error', '')}
            return rows
        except (ValueError, TypeError, AssertionError) as exc:
            return {op['id']: {**default, 'status': 'error', 'detail': f'Invalid adapter JSON: {exc}'} for op in operations}


def grade(submission: Path, context: GradeContext) -> dict:
    tests = []

    def result(name, category, points, check=None, *, skip=None):
        if skip:
            tests.append(TestResult(name, category, 'skipped', None, points, detail=skip))
            return
        try:
            detail = check() if check else None
            tests.append(TestResult(name, category, 'passed', points, points, detail=detail or 'Verified by independent grader'))
        except (AssertionError, ValueError, TypeError, KeyError, IndexError) as exc:
            tests.append(TestResult(name, category, 'failed', 0, points, detail=str(exc)[:3000]))

    def criterion(name, category, points, operations, observed, check):
        unavailable = [observed[op['id']] for op in operations if observed[op['id']]['status'] == 'skipped']
        def verify():
            for op in operations:
                row = observed[op['id']]
                require(row['status'] == 'ok', f'{op["id"]}: {row.get("detail", "execution error")}')
                try:
                    check(op, row)
                except (AssertionError, ValueError, TypeError, KeyError, IndexError) as exc:
                    raise AssertionError(f'{op["id"]}: {exc}') from exc
            return f'Checked {len(operations)} independent inputs'
        result(name, category, points, verify, skip=unavailable[0].get('detail') or 'Node/Docker unavailable' if unavailable else None)
        if category == 'performance':
            times = [observed[op['id']].get('duration_ms') for op in operations]
            if times and all(isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) and t >= 0 for t in times):
                tests[-1].duration_seconds = sum(times) / 1000
                tests[-1].detail += f'; max core call {max(times):.3f}ms (excludes animation, Docker startup and serialization)'

    for filename, points in [('index.html', 2), ('maze-core.js', 2)]:
        result('entry_' + filename, 'artifacts', points,
               lambda filename=filename: require((submission / filename).is_file() and (submission / filename).stat().st_size > 0, filename + ' missing or empty'))
    files = list(regular_files(submission))
    result('readme_present', 'documentation', 1,
           lambda: require(any(p.name.lower().startswith('readme') and p.stat().st_size > 0 for p in files), 'README missing/empty; presence check only'))
    result('self_test_sources_present', 'documentation', 1,
           lambda: require(any(p.suffix in {'.js', '.cjs', '.mjs'} and (p.stem.lower().startswith(('test', 'spec')) or re.search(r'(?:[._-])(?:tests?|spec)(?:$|[._-])', p.stem.lower()) or 'tests' in p.relative_to(submission).parts) for p in files), 'No recognizable self-test source; self-tests are not executed or scored as independent correctness'))
    entry = None
    if (submission / 'index.html').is_file():
        try:
            entry, missing, remote = asset_checks(submission)
            result('local_asset_references', 'artifacts', 2, lambda: require(not missing, 'Missing/outside local assets: ' + ', '.join(missing)[:1500]))
            result('no_literal_external_dependencies', 'artifacts', 2, lambda: require(not remote, 'External dependencies: ' + ', '.join(remote)[:1500]))
        except (BenchError, OSError, ValueError) as exc:
            result('local_asset_references', 'artifacts', 2, skip=str(exc))
            result('no_literal_external_dependencies', 'artifacts', 2, skip=str(exc))
    else:
        result('local_asset_references', 'artifacts', 2, lambda: require(False, 'index.html missing'))
        result('no_literal_external_dependencies', 'artifacts', 2, skip='No HTML entry to inspect')

    runtime = Runtime(submission, context) if (submission / 'maze-core.js').is_file() else None
    gen_ops, groups = generation_plan()
    generation = runtime.batch('generation', gen_ops) if runtime else {op['id']: {'status': 'error', 'detail': 'maze-core.js missing'} for op in gen_ops}
    first = gen_ops[:1]
    criterion('commonjs_api', 'api', 3, first, generation,
              lambda op, row: graph(row['value'], op['options']['width'], op['options']['height']))

    for algorithm, operations in groups.items():
        baseline = [op for op in operations if '_base_' in op['id']]
        edges = [op for op in operations if '_edge_' in op['id']]
        criterion(f'{algorithm}_representation_and_boundaries', 'generation', 1.5, baseline + edges, generation,
                  lambda op, row: graph(row['value'], op['options']['width'], op['options']['height']))
        criterion(f'{algorithm}_reciprocal_walls', 'generation', 2, operations, generation,
                  lambda op, row: graph(row['value'], op['options']['width'], op['options']['height']))
        criterion(f'{algorithm}_degenerate_sizes', 'generation', 0.5, edges, generation,
                  lambda op, row: require(all(not math.isinf(d) for d in distances(row['value'], 0)), 'one-cell/one-dimensional maze is disconnected'))
        criterion(f'{algorithm}_connectivity', 'generation', 3, baseline, generation,
                  lambda op, row: require(all(not math.isinf(d) for d in distances(row['value'], 0)), 'not all cells are connected'))
        criterion(f'{algorithm}_perfect_maze', 'generation', 3, baseline, generation,
                  lambda op, row: require(sum(map(len, graph(row['value']))) // 2 == len(row['value']['cells']) - 1 and all(not math.isinf(d) for d in distances(row['value'], 0)), 'Perfect Maze must be a connected tree with V-1 edges'))
        repeated = [op for op in operations if op['id'].endswith(('weight_1', 'repeat_a', 'repeat_b'))]
        criterion(f'{algorithm}_seed_reproducibility', 'generation', 2, repeated, generation,
                  lambda op, row: require(maze_identity(row['value']) == maze_identity(generation[f'{algorithm}_weight_1'].get('value')), 'same options/seed changed after interleaved calls'))
        loops = [op for op in operations if '_loop_' in op['id']]
        def check_loops(op, row):
            maze = row['value']
            adjacency = graph(maze)
            v = len(adjacency)
            internal = (maze['width'] - 1) * maze['height'] + (maze['height'] - 1) * maze['width']
            remaining = internal - (v - 1)
            opened = sum(map(len, adjacency)) // 2 - (v - 1)
            expected = remaining * op['options']['loops']
            require(opened in {math.floor(expected), math.ceil(expected)}, f'loops must open a fraction of remaining walls: got {opened}, expected floor/ceil({expected})')
            require(all(not math.isinf(d) for d in distances(maze, 0)), 'looped maze is disconnected')
        criterion(f'{algorithm}_loop_ratio', 'generation', 1.5, loops, generation, check_loops)
        weighted = [op for op in operations if '_weight_' in op['id']]
        def check_weights(op, row):
            graph(row['value'])
            require(len({cell['cost'] for cell in row['value']['cells']}) > 1, 'weighted option must actually vary costs in this multi-cell fixture')
        criterion(f'{algorithm}_seeded_weights', 'generation', 1.5, weighted, generation, check_weights)
    # The unweighted cost rule is part of representation and must be explicitly verified.
    for test in tests:
        if test.name.endswith('_representation_and_boundaries') and test.status == 'passed':
            algorithm = test.name.split('_', 1)[0]
            if any(any(cell['cost'] != 1 for cell in generation[op['id']]['value']['cells']) for op in groups[algorithm] if not op['options']['weighted']):
                test.status, test.score, test.detail = 'failed', 0, 'Unweighted generation must set every cost to 1'

    cases = search_cases()
    search_ops = [{'id': f'{name}_{algorithm}', 'kind': 'solve', 'maze': maze,
                   'options': {'algorithm': algorithm, 'start': start, 'end': end}}
                  for name, maze, start, end in cases for algorithm in ALGORITHMS]
    # Repeated solve calls after unrelated calls expose solver state leaks.
    for algorithm in ALGORITHMS:
        op = copy.deepcopy(next(op for op in search_ops if op['id'] == 'weighted_detour_' + algorithm))
        op['id'] = 'repeat_' + algorithm
        search_ops.append(op)
    solved = runtime.batch('search', search_ops) if runtime else {op['id']: {'status': 'error', 'detail': 'maze-core.js missing'} for op in search_ops}
    for algorithm, points in [('bfs', 7), ('dijkstra', 7), ('astar', 7)]:
        selected = [op for op in search_ops if op['options']['algorithm'] == algorithm]
        criterion(algorithm + '_optimal_paths', 'search', points, selected, solved,
                  lambda op, row: check_solution(op['maze'], op['options'], row['value']))
    criterion('bfs_actual_entered_cell_cost', 'search', 4, [op for op in search_ops if op['options']['algorithm'] == 'bfs'], solved,
              lambda op, row: check_solution(op['maze'], op['options'], row['value'], optimal=False))
    criterion('unreachable_contract', 'edge_cases', 3, [op for op in search_ops if op['id'].startswith(('unreachable_', 'all_blocked_'))], solved,
              lambda op, row: check_solution(op['maze'], op['options'], row['value']))
    criterion('same_start_end_contract', 'edge_cases', 3, [op for op in search_ops if op['id'].startswith(('same_endpoint_', 'single_cell_'))], solved,
              lambda op, row: check_solution(op['maze'], op['options'], row['value']))
    criterion('solver_does_not_mutate_input', 'state', 4, search_ops, solved,
              lambda op, row: require(row.get('unchanged') is True and row.get('input_after') == op['maze'], 'solveMaze changed input maze/costs'))
    criterion('valid_expansion_order', 'search', 3, search_ops, solved,
              lambda op, row: check_visited(op['maze'], op['options'], row['value']))
    repeats = [op for op in search_ops if op['id'].startswith('repeat_')]
    criterion('solver_repeatability', 'state', 2, repeats, solved,
              lambda op, row: require(solution_identity(row['value']) == solution_identity(solved['weighted_detour_' + op['options']['algorithm']]['value']), 'same maze/options returned different calculation results'))

    browser_ops = [{'id': 'browser_maze', 'kind': 'generate', 'options': {'width': 3, 'height': 3, 'algorithm': 'dfs', 'seed': 17, 'loops': 0, 'weighted': False}},
                   {'id': 'browser_solution', 'kind': 'solve', 'maze_id': 'browser_maze', 'options': {'algorithm': 'bfs', 'start': 0, 'end': 8}}]
    dependencies = []
    if entry:
        for script in entry.scripts:
            script = urljoin(entry.base, script)
            script_path = unquote(urlsplit(script).path)
            if Path(script_path).name == 'maze-core.js':
                break
            if script_path and not urlsplit(script).scheme and not urlsplit(script).netloc and (submission / script_path).is_file():
                dependencies.append(script_path)
    browser = runtime.batch('browser_namespace', browser_ops, mode='browser', dependencies=dependencies) if runtime else {op['id']: {'status': 'error', 'detail': 'maze-core.js missing'} for op in browser_ops}
    # vm is a classic-script compatibility probe, not an actual browser/ESM loader.
    if entry and (entry.module_scripts or entry.inline_scripts) and any(row['status'] == 'error' for row in browser.values()):
        result('window_mazecore_classic_script_api', 'api', 2, skip='ES module/inline browser bootstrap needs a real browser; classic-script VM could not verify this submission')
    else:
        criterion('window_mazecore_classic_script_api', 'api', 2, browser_ops, browser,
                  lambda op, row: graph(row['value'], 3, 3) if op['kind'] == 'generate' else check_solution(browser['browser_maze']['value'], op['options'], row['value']))

    perf_ops = []
    for algorithm in ('dfs', 'prim'):
        perf_ops.append({'id': 'large_' + algorithm, 'kind': 'generate', 'options': {'width': 80, 'height': 80, 'algorithm': algorithm, 'seed': 72, 'loops': 0.25, 'weighted': True}})
        for search in ALGORITHMS:
            perf_ops.append({'id': 'large_' + algorithm + '_' + search, 'kind': 'solve', 'maze_id': 'large_' + algorithm,
                             'options': {'algorithm': search, 'start': 0, 'end': 6399}})
    performance = runtime.batch('large_grid', perf_ops) if runtime else {op['id']: {'status': 'error', 'detail': 'maze-core.js missing'} for op in perf_ops}
    def check_performance(op, row):
        duration = row.get('duration_ms')
        require(isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and 0 <= duration <= 2000,
                f'core function exceeded the grading budget of 2000ms: {duration}')
        if op['kind'] == 'generate':
            maze = row['value']
            graph(maze, 80, 80)
            require(all(not math.isinf(d) for d in distances(maze, 0)), 'large generated maze disconnected')
        else:
            maze = performance[op['maze_id']].get('value')
            check_solution(maze, op['options'], row['value'])
    criterion('large_grid_correctness_and_compute_budget', 'performance', 5, perf_ops, performance, check_performance)
    for name in ['offline_browser_startup', 'controls_and_comparison_table', 'animation_pause_resume_step_cancel',
                 'responsive_canvas_and_color_legend', 'generation_responsiveness_and_stale_animation']:
        result(name, 'browser_ui', 2, skip='No real browser interaction in this grader. Requires separate browser/manual verification; not inferred from source keywords.')
    result('generator_algorithm_identity', 'manual_review', 0, skip='Output invariants do not prove that DFS and Prim are genuinely different implementations; source review required.')
    result('astar_manhattan_heuristic_design', 'manual_review', 0, skip='Optimal output is verified; the use/admissibility of a Manhattan heuristic itself requires source review.')
    if runtime:
        context.artifact_dir.mkdir(parents=True, exist_ok=True)
        write_json(context.artifact_dir / 'maze-executions.json', runtime.executions)
        write_json(context.artifact_dir / 'maze-compute-times.json', runtime.measurements)
    return aggregate(tests)
