"""All local JS execution below is LIMITED to our authored trusted fixture/mutants.

The production grader has no local/unsafe backend and always isolates candidates.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.grading import GradeContext, validate_grade
from codex_bench.util import copy_tree
from tests.helpers import test_directory

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / 'graders' / 'maze'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'maze_reference'
spec = importlib.util.spec_from_file_location('maze_grader_under_test', PLUGIN_DIR / 'plugin.py')
maze = importlib.util.module_from_spec(spec)
with patch('sys.dont_write_bytecode', True):
    spec.loader.exec_module(maze)


class TrustedFixtureExecutor:
    def __init__(self, submission):
        self.submission = submission

    def run(self, name, arguments, payload):
        # This adapter exists only in the test suite and cannot be selected in config.json.
        request = {**payload, 'root': str(self.submission)}
        process = subprocess.run([shutil.which('node'), *arguments[1:]], input=json.dumps(request).encode(),
                                 capture_output=True, timeout=15)
        return {'status': 'ok' if process.returncode == 0 else 'error',
                'stdout': process.stdout.decode('utf-8', errors='replace'),
                'stderr': process.stderr.decode('utf-8', errors='replace'), 'detail': '',
                'duration_seconds': 0.1, 'exit_code': process.returncode}


class OracleTests(unittest.TestCase):
    def test_entering_cell_cost_and_weighted_detour(self):
        _, graph, start, end = maze.search_cases()[0]
        self.assertEqual(maze.distances(graph, start)[end], 2)
        self.assertEqual(maze.distances(graph, start, True)[end], 4)
        maze.check_solution(graph, {'algorithm': 'bfs', 'start': 0, 'end': 2},
                            {'found': True, 'path': [0, 1, 2], 'cost': 10})
        maze.check_solution(graph, {'algorithm': 'dijkstra', 'start': 0, 'end': 2},
                            {'found': True, 'path': [0, 3, 4, 5, 2], 'cost': 4})

    def test_rejects_asymmetric_walls_and_open_boundary(self):
        grid = maze.make_maze(2, 1, [(0, 1)])
        grid['cells'][1]['walls'] |= 8
        with self.assertRaisesRegex(AssertionError, 'reciprocal'):
            maze.graph(grid)
        grid = maze.make_maze(1, 1, [])
        grid['cells'][0]['walls'] = 14
        with self.assertRaisesRegex(AssertionError, 'boundary'):
            maze.graph(grid)

    def test_rejects_bad_shapes_costs_and_diagonal_path(self):
        grid = maze.make_maze(2, 2, maze.all_edges(2, 2))
        with self.assertRaisesRegex(AssertionError, 'diagonally'):
            maze.check_solution(grid, {'start': 0, 'end': 3, 'algorithm': 'bfs'}, {'found': True, 'path': [0, 3], 'cost': 1})
        for cost in [True, 0, 10, 1.5, None]:
            grid['cells'][0]['cost'] = cost
            with self.subTest(cost=cost), self.assertRaises(AssertionError):
                maze.graph(grid)

    def test_unreachable_and_same_endpoint_contract(self):
        grid = maze.make_maze(2, 1, [], [9, 2])
        maze.check_solution(grid, {'algorithm': 'dijkstra', 'start': 0, 'end': 1}, {'found': False, 'path': [], 'cost': None})
        maze.check_solution(grid, {'algorithm': 'astar', 'start': 0, 'end': 0}, {'found': True, 'path': [0], 'cost': 0})
        with self.assertRaises(AssertionError):
            maze.check_solution(grid, {'algorithm': 'bfs', 'start': 0, 'end': 1}, {'found': False, 'path': [], 'cost': 0})

    def test_bfs_visited_order_is_expansion_order(self):
        grid = maze.make_maze(3, 1, [(0, 1), (1, 2)])
        options = {'start': 0, 'end': 2, 'algorithm': 'bfs'}
        maze.check_visited(grid, options, {'visitedOrder': [0, 1, 2]})
        with self.assertRaises(AssertionError):
            maze.check_visited(grid, options, {'visitedOrder': [0, 2, 1]})

    def test_random_oracles_are_repeatable(self):
        self.assertEqual(maze.search_cases(), maze.search_cases())


@unittest.skipUnless(shutil.which('node'), 'Node needed only to validate our trusted JS fixture')
class MazeGraderTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.submission = self.root / 'submission'
        copy_tree(FIXTURE, self.submission)
        self.context = GradeContext(self.root / 'logs', PLUGIN_DIR, 5, {})

    def tearDown(self):
        self.temp.cleanup()

    def grade(self):
        with patch.object(GradeContext, 'docker', return_value=TrustedFixtureExecutor(self.submission)):
            return validate_grade(maze.grade(self.submission, self.context))

    def mutate(self, text):
        path = self.submission / 'maze-core.js'
        path.write_text(path.read_text(encoding='utf-8') + '\n' + text + '\n', encoding='utf-8')

    def failures(self, grade):
        return {test['name'] for test in grade['tests'] if test['status'] == 'failed'}

    def test_trusted_fixture_scores_90_and_ui_remains_unverified(self):
        grade = self.grade()
        self.assertEqual(grade['planned_max_score'], 100)
        self.assertEqual(grade['score'], 90, self.failures(grade))
        self.assertEqual(grade['max_score'], 90)
        self.assertEqual(grade['skipped_tests'], 7)
        self.assertTrue(all(t['status'] == 'skipped' and t['score'] is None for t in grade['tests'] if t['category'] == 'browser_ui'))
        self.assertTrue((self.context.artifact_dir / 'maze-executions.json').is_file())

    def test_mutation_is_detected_independently(self):
        self.mutate("const originalSolve = module.exports.solveMaze; module.exports.solveMaze = (m,o) => { const result = originalSolve(m,o); m.cells[0].cost = m.cells[0].cost === 9 ? 8 : 9; return result; };")
        self.assertIn('solver_does_not_mutate_input', self.failures(self.grade()))

    def test_charging_start_or_reporting_hops_as_cost_is_detected(self):
        self.mutate("const originalSolve = module.exports.solveMaze; module.exports.solveMaze = (m,o) => { const r=originalSolve(m,o); if(r.found)r.cost=r.path.length-1; return r; };")
        failed = self.failures(self.grade())
        self.assertIn('bfs_actual_entered_cell_cost', failed)
        self.assertIn('dijkstra_optimal_paths', failed)

    def test_bfs_disguised_as_weighted_search_is_detected(self):
        self.mutate("const originalSolve = module.exports.solveMaze; module.exports.solveMaze = (m,o) => originalSolve(m,{...o,algorithm:'bfs'});")
        failed = self.failures(self.grade())
        self.assertIn('dijkstra_optimal_paths', failed)
        self.assertIn('astar_optimal_paths', failed)
        self.assertNotIn('bfs_optimal_paths', failed)

    def test_ignoring_loops_or_weighted_options_is_detected(self):
        self.mutate("const originalGenerate = module.exports.generateMaze; module.exports.generateMaze = o => originalGenerate({...o,loops:0,weighted:false});")
        failed = self.failures(self.grade())
        self.assertIn('dfs_loop_ratio', failed)
        self.assertIn('prim_seeded_weights', failed)

    def test_invalid_wall_boundary_is_not_given_connectivity_credit(self):
        self.mutate("const originalGenerate = module.exports.generateMaze; module.exports.generateMaze = o => {const r=originalGenerate(o);r.cells[0].walls &= ~1;return r;};")
        self.assertIn('dfs_representation_and_boundaries', self.failures(self.grade()))

    def test_missing_core_has_failed_core_tests_without_candidate_execution(self):
        (self.submission / 'maze-core.js').unlink()
        with patch.object(GradeContext, 'docker', side_effect=AssertionError('Missing core must not execute')):
            grade = validate_grade(maze.grade(self.submission, self.context))
        self.assertIn('entry_maze-core.js', self.failures(grade))
        self.assertIn('commonjs_api', self.failures(grade))

    def test_infrastructure_unavailable_preserves_null_scores(self):
        class Missing:
            def run(self, *args, **kwargs):
                return {'status': 'skipped', 'detail': 'Docker image not installed', 'duration_seconds': None}
        with patch.object(GradeContext, 'docker', return_value=Missing()):
            grade = validate_grade(maze.grade(self.submission, self.context))
        self.assertEqual(grade['score'], 10)
        self.assertEqual(grade['max_score'], 10)
        self.assertTrue(all(t['score'] is None for t in grade['tests'] if t['category'] in {'generation', 'search', 'api', 'state', 'performance', 'edge_cases'}))

    def test_adapter_failure_cannot_be_reported_as_success(self):
        class Broken:
            def run(self, *args, **kwargs):
                return {'status': 'ok', 'stdout': '{"api":true,"results":[]}', 'detail': '', 'duration_seconds': 0.1}
        with patch.object(GradeContext, 'docker', return_value=Broken()):
            grade = validate_grade(maze.grade(self.submission, self.context))
        self.assertIn('bfs_optimal_paths', self.failures(grade))
        self.assertIn('large_grid_correctness_and_compute_budget', self.failures(grade))

    def test_remote_assets_fail_without_network_access(self):
        with (self.submission / 'index.html').open('a', encoding='utf-8') as file:
            file.write('<script src="https://example.invalid/library.js"></script>')
        _, missing, remote = maze.asset_checks(self.submission)
        self.assertIn('https://example.invalid/library.js', remote)

    def test_missing_local_asset_and_parent_traversal_are_detected(self):
        (self.submission / 'index.html').write_text('<link rel="stylesheet" href="missing.css"><script src="../outside.js"></script>')
        _, missing, _ = maze.asset_checks(self.submission)
        self.assertTrue(any('missing.css' in value for value in missing))
        self.assertTrue(any('outside submission' in value for value in missing))

    def test_commonjs_node_builtins_in_self_tests_are_allowed(self):
        _, missing, remote = maze.asset_checks(self.submission)
        self.assertFalse(missing)
        self.assertFalse(remote)

    def test_comment_urls_and_readme_links_are_not_runtime_dependencies(self):
        self.mutate('// fetch("https://example.invalid")\n/* import "https://example.invalid"; */')
        _, missing, remote = maze.asset_checks(self.submission)
        self.assertFalse(remote)

    def test_nondeterministic_generation_is_detected(self):
        self.mutate("const originalGenerate = module.exports.generateMaze; let serial=0; module.exports.generateMaze = o => originalGenerate({...o,seed:(o.seed+serial++)>>>0});")
        failed = self.failures(self.grade())
        self.assertIn('dfs_seed_reproducibility', failed)
        self.assertIn('prim_seed_reproducibility', failed)

    def test_extra_timing_metadata_is_not_part_of_determinism_contract(self):
        self.mutate("if(typeof module!=='undefined'){const originalGenerate=module.exports.generateMaze; const originalSolve=module.exports.solveMaze; let serial=0; module.exports.generateMaze=o=>({...originalGenerate(o),elapsedMs:serial++}); module.exports.solveMaze=(m,o)=>({...originalSolve(m,o),elapsedMs:serial++});}")
        grade = self.grade()
        self.assertEqual(grade['score'], 90, self.failures(grade))

    def test_performance_threshold_uses_measured_function_times(self):
        base = TrustedFixtureExecutor(self.submission)
        class Slow:
            def run(inner, name, arguments, payload):
                response = base.run(name, arguments, payload)
                if name == 'large_grid':
                    data = json.loads(response['stdout'])
                    data['results'][0]['duration_ms'] = 2500
                    response['stdout'] = json.dumps(data)
                return response
        with patch.object(GradeContext, 'docker', return_value=Slow()):
            grade = validate_grade(maze.grade(self.submission, self.context))
        self.assertIn('large_grid_correctness_and_compute_budget', self.failures(grade))
        self.assertGreater(next(t for t in grade['tests'] if t['category'] == 'performance')['duration_seconds'], 2.5)

    def test_commonjs_only_has_no_browser_api_credit(self):
        path = self.submission / 'maze-core.js'
        path.write_text(path.read_text(encoding='utf-8').replace('if (root) root.MazeCore = api;', ''), encoding='utf-8')
        self.assertIn('window_mazecore_classic_script_api', self.failures(self.grade()))

    def test_esm_browser_bootstrap_is_unmeasured_when_vm_cannot_run_it(self):
        path = self.submission / 'maze-core.js'
        path.write_text(path.read_text(encoding='utf-8').replace('if (root) root.MazeCore = api;', ''), encoding='utf-8')
        (self.submission / 'index.html').write_text('<script type="module">import core from "./maze-core.js"; window.MazeCore=core;</script>')
        test = next(t for t in self.grade()['tests'] if t['name'] == 'window_mazecore_classic_script_api')
        self.assertEqual(test['status'], 'skipped')
        self.assertIsNone(test['score'])

    def test_candidate_exit_code_cannot_impersonate_missing_docker(self):
        class Exit:
            def run(self, *args):
                return {'status': 'skipped', 'exit_code': 125, 'detail': '', 'stderr': '', 'duration_seconds': 0.1}
        with patch.object(GradeContext, 'docker', return_value=Exit()):
            grade = validate_grade(maze.grade(self.submission, self.context))
        self.assertIn('commonjs_api', self.failures(grade))

    def test_default_image_is_node_without_mutating_context(self):
        options = []
        def docker(context, submission):
            options.append(context.options)
            class Missing:
                def run(self, *args):
                    return {'status': 'skipped', 'detail': 'missing image'}
            return Missing()
        with patch.object(GradeContext, 'docker', docker):
            maze.grade(self.submission, self.context)
        self.assertEqual(options[0]['docker_image'], 'node:22-bookworm-slim')
        self.assertEqual(self.context.options, {})

    def test_local_and_remote_base_urls_are_handled(self):
        (self.submission / 'assets').mkdir()
        (self.submission / 'assets' / 'app.js').write_text('// local fixture')
        (self.submission / 'index.html').write_text('<base href="assets/"><script src="app.js"></script>')
        _, missing, remote = maze.asset_checks(self.submission)
        self.assertFalse(missing)
        self.assertFalse(remote)
        (self.submission / 'index.html').write_text('<base href="https://example.invalid/"><script src="app.js"></script>')
        _, _, remote = maze.asset_checks(self.submission)
        self.assertTrue(remote)
