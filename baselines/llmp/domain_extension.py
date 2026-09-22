"""LLM+P v2: compile the shared finite domain and invoke real Fast Downward.

No primitive effects are authored here. Grounded actor/entity identities map
bijectively to PDDL action symbols; one conditional batch offload remains one
planner action and one physical dispatch. ADL cardinality is a finite goal
formula, never an extra robot action. The legacy v1 PDDL is untouched.

The supplied DomainProblem is the translation result. An offline fixture is
not a model call; FD only certifies that supplied goal. Intended-goal checking
belongs to the common wrapper and is never attributed to the native planner.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import replace
from itertools import combinations
import math
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
import uuid
from baselines.common.native_result import DEPENDENCY_REQUIRED
from baselines.common.native_result import NATIVE_EXHAUSTED
from baselines.common.native_result import NATIVE_INVALID_OUTPUT
from baselines.common.native_result import NATIVE_SOLVED
from baselines.common.native_result import NATIVE_UNSOLVABLE
from baselines.common.native_result import NativePlanResult
IMPLEMENTATION_ID = 'llmp_shared_domain_fd_v2'
PDDL_DOMAIN = 'paper4-finite-domain-extension-v1'
FD_IMAGE = 'aibasel/downward@sha256:99eb54a2e55c76eb950f53a78899c9f5800f138ff604b6a657b5f977623bfd4d'
FD_VERSION_DISCLOSURE = "installed image created 2025-02-07; not the paper's 22.12.0 checkout"
DEFAULT_SEARCH = 'eager_greedy([ff()])'
NATIVE_TIMEOUT = 'NATIVE_TIMEOUT'
NATIVE_RESOURCE_LIMIT = 'NATIVE_RESOURCE_LIMIT'
NATIVE_INPUT_ERROR = 'NATIVE_INPUT_ERROR'
NATIVE_UNSUPPORTED = 'NATIVE_UNSUPPORTED'
NATIVE_PLANNER_ERROR = 'NATIVE_PLANNER_ERROR'
_RESOURCE_WRAPPER = "import json, subprocess, sys, time\nfrom pathlib import Path\nstarted = time.perf_counter()\ndef events():\n    try:\n        return {k: int(v) for k, v in (line.split() for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())}\n    except (OSError, ValueError):\n        return None\nbefore = events()\ncode = subprocess.run(sys.argv[1:], check=False).returncode\nafter = events()\nresult = {'fd_returncode': code, 'container_wall_s': time.perf_counter() - started,\n          'memory_peak_bytes': None, 'memory_max_bytes': None, 'memory_peak_source': None,\n          'memory_events_before': before, 'memory_events_after': after,\n          'memory_events_delta': {k: after[k] - before.get(k, 0) for k in after} if before is not None and after is not None else None,\n          'memory_accounting': 'Linux cgroup v2 memory.peak: container tree including wrapper and file cache; not Windows committed memory'}\ntry:\n    result['memory_peak_bytes'] = int(Path('/sys/fs/cgroup/memory.peak').read_text().strip())\n    result['memory_peak_source'] = '/sys/fs/cgroup/memory.peak'\nexcept (OSError, ValueError) as exc:\n    result['memory_peak_unavailable_reason'] = type(exc).__name__\ntry:\n    value = Path('/sys/fs/cgroup/memory.max').read_text().strip()\n    result['memory_max_bytes'] = None if value == 'max' else int(value)\nexcept (OSError, ValueError):\n    pass\nPath('/wd/resource_metrics.json').write_text(json.dumps(result, sort_keys=True), encoding='utf-8')\nsys.exit(code if code >= 0 else 128 - code)\n"

def _read_resources(run_dir, artifact, *, unavailable_reason=None):
    target = run_dir / 'resource_metrics.json'
    try:
        measured = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        artifact['resources']['memory_peak_unavailable_reason'] = unavailable_reason or 'container exited without final cgroup sample'
        return
    artifact['resources'].update(measured)
    artifact['resources']['metrics_path'] = str(target)
    counters = measured.get('memory_events_delta') or {}
    artifact['resources']['memory_max_event_count'] = counters.get('max')
    artifact['resources']['memory_pressure_observed'] = counters.get('max', 0) > 0
    if counters.get('oom_kill', 0) or counters.get('oom_group_kill', 0):
        artifact['resources']['limit_reason'] = 'cgroup_oom_kill'
    elif counters.get('oom', 0):
        artifact['resources']['limit_reason'] = 'cgroup_oom_allocation_failure'

@dataclass(frozen=True)
class CompiledPDDL:
    domain_text: str
    problem_text: str
    fact_symbols: dict[str, str]
    actions_by_symbol: dict[str, Any]

def _literals(positive, negative, symbols):
    return [f'({symbols[f]})' for f in sorted(positive)] + [f'(not ({symbols[f]}))' for f in sorted(negative)]

def compile_pddl(problem) -> CompiledPDDL:
    """Finite, fully grounded ADL compiler; facts are opaque identity-bearing strings."""
    actions = tuple(problem.actions)
    ids = [a.action_id for a in actions]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate ground action identity')
    cardinality = tuple(getattr(problem, 'goal_cardinality', ()))
    negative_goal = frozenset(getattr(problem, 'negative_goal_facts', ()))
    facts = set(problem.initial_state) | set(problem.goal_facts) | set(negative_goal)
    for threshold, atoms in cardinality:
        if type(threshold) is not int or threshold < 0:
            raise ValueError('cardinality threshold must be a nonnegative integer')
        facts.update(atoms)
    for action in actions:
        for name in ('preconditions', 'negative_preconditions', 'add_effects', 'delete_effects'):
            facts.update(getattr(action, name))
        for effect in action.conditional_effects:
            for name in ('conditions', 'negative_conditions', 'add_effects', 'delete_effects'):
                facts.update(getattr(effect, name))
    if any((not isinstance(fact, str) or not fact for fact in facts)):
        raise ValueError('finite facts must be nonempty strings')
    symbols = {fact: f'p{i:05d}' for i, fact in enumerate(sorted(facts))}
    mapped = {f'a{i:05d}': action for i, action in enumerate(sorted(actions, key=lambda a: a.action_id))}
    predicates = ' '.join((f'({symbol})' for symbol in symbols.values()))
    predicates += ' (unreachable-cardinality-goal)'
    rendered_actions = []
    for symbol, action in mapped.items():
        conditions = _literals(action.preconditions, action.negative_preconditions, symbols)
        effects = _literals(action.add_effects, action.delete_effects, symbols)
        for effect in action.conditional_effects:
            condition = ' '.join(_literals(effect.conditions, effect.negative_conditions, symbols))
            consequence = ' '.join(_literals(effect.add_effects, effect.delete_effects, symbols))
            effects.append(f'(when (and {condition}) (and {consequence}))')
        rendered_actions.append(f"  (:action {symbol}\n    :parameters ()\n    :precondition (and {' '.join(conditions)})\n    :effect (and {' '.join(effects)}))")
    domain = f'(define (domain {PDDL_DOMAIN})\n  (:requirements :adl)\n  (:predicates {predicates})\n' + '\n'.join(rendered_actions) + '\n)\n'
    goals = _literals(problem.goal_facts, negative_goal, symbols)
    for threshold, atoms in cardinality:
        if threshold > len(atoms):
            goals.append('(unreachable-cardinality-goal)')
        elif threshold:
            terms = ['(and ' + ' '.join((f'({symbols[f]})' for f in group)) + ')' for group in combinations(sorted(atoms), threshold)]
            goals.append('(or ' + ' '.join(terms) + ')')
    init = ' '.join((f'({symbols[f]})' for f in sorted(problem.initial_state)))
    text = f"(define (problem paper4-finite-problem)\n  (:domain {PDDL_DOMAIN})\n  (:init {init})\n  (:goal (and {' '.join(goals)}))\n)\n"
    return CompiledPDDL(domain, text, symbols, mapped)

class PlanParseError(ValueError):
    pass

def parse_ground_plan(text, compiled):
    """Parse every non-comment line strictly; never silently discard unknown output."""
    result = []
    for line in text.splitlines():
        line = line.split(';', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch('\\(\\s*([a-z][a-z0-9-]*)\\s*\\)', line)
        if match is None:
            raise PlanParseError(f'malformed grounded planner action: {line}')
        symbol = match.group(1)
        if symbol not in compiled.actions_by_symbol:
            raise PlanParseError(f'unknown grounded planner action: {symbol}')
        result.append(compiled.actions_by_symbol[symbol])
    return tuple(result)

def _work_metrics(stdout):
    """Actual FD counters when printed, unknown otherwise; no synthetic work counts."""
    result = {}
    for name, pattern in {'expanded_states': 'Expanded (\\d+) state', 'generated_states': 'Generated (\\d+) state', 'evaluated_states': 'Evaluated (\\d+) state', 'plan_length': 'Plan length: (\\d+) step', 'plan_cost': 'Plan cost: (\\d+)', 'search_time_s': 'Search time: ([0-9.]+)s', 'total_time_s': 'Total time: ([0-9.]+)s'}.items():
        matches = re.findall(pattern, stdout)
        result[name] = (float(matches[-1]) if name.endswith('_s') else int(matches[-1])) if matches else None
    return result

def _result(status, artifact, *, plan=(), reason='', **diagnostics):
    return NativePlanResult('llmp', IMPLEMENTATION_ID, status, tuple(plan), artifact, {'reason': reason, 'native_goal_certified': status == NATIVE_SOLVED, 'native_certifies_only_supplied_goal': True, 'intended_goal_checked_by_native': False, 'intended_goal_wrapper_credited_to_baseline': False, **diagnostics})

def classify_fd_failure(returncode):
    """Exit codes read from the installed image's driver/returncodes.py.

    In particular 12 is incomplete search, NOT unsolvable (10/11).
    """
    if returncode in (10, 11):
        return NATIVE_UNSOLVABLE
    if returncode == 12:
        return NATIVE_EXHAUSTED
    if returncode in (21, 23):
        return NATIVE_TIMEOUT
    if returncode in (20, 22, 24, 137):
        return NATIVE_RESOURCE_LIMIT
    if returncode in (31, 33, 36):
        return NATIVE_INPUT_ERROR
    if returncode in (34, 37):
        return NATIVE_UNSUPPORTED
    if returncode in (125, 126, 127):
        return DEPENDENCY_REQUIRED
    return NATIVE_PLANNER_ERROR

def solve_fd(problem, workdir, *, timeout_s=30.0, memory_mb=2048, docker_binary=None, image=FD_IMAGE, search=DEFAULT_SEARCH, translation_source='offline_goal_translation_fixture', translated_goal=None, hard_wall_s=None, record_resources=None):
    """Compile and solve using the installed FD Docker image, without network access.

    Each solve owns a new local directory so interrupted/stale sas_plan files can
    never be reused. Search is bounded explicitly; no hidden retry or alternate
    planner is attempted. A caller may reuse workdir safely across candidates.
    """
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError('timeout_s must be positive and finite')
    if hard_wall_s is not None and (not math.isfinite(hard_wall_s) or hard_wall_s <= 0):
        raise ValueError('hard_wall_s must be positive and finite')
    hard_started = time.perf_counter()
    measure_resources = hard_wall_s is not None if record_resources is None else bool(record_resources)
    if type(memory_mb) is not int or memory_mb <= 0:
        raise ValueError('memory_mb must be a positive integer')
    intended_goal = {'facts': sorted(problem.goal_facts), 'negative': sorted(getattr(problem, 'negative_goal_facts', ())), 'cardinality': [{'threshold': k, 'atoms': sorted(atoms)} for k, atoms in getattr(problem, 'goal_cardinality', ())]}
    if translated_goal is not None:
        if set(translated_goal) - {'goal_facts', 'negative_goal_facts', 'goal_cardinality'}:
            raise ValueError('translated_goal only replaces goal conditions')
        problem = replace(problem, goal_facts=frozenset(translated_goal.get('goal_facts', ())), negative_goal_facts=frozenset(translated_goal.get('negative_goal_facts', ())), goal_cardinality=tuple(((k, frozenset(atoms)) for k, atoms in translated_goal.get('goal_cardinality', ()))))
    artifact = {'domain_version': problem.domain_version, 'scenario_id': problem.scenario_id, 'intended_goal_for_wrapper': intended_goal, 'supplied_goal_facts': sorted(problem.goal_facts), 'supplied_negative_goal_facts': sorted(getattr(problem, 'negative_goal_facts', ())), 'supplied_goal_cardinality': [{'threshold': k, 'atoms': sorted(atoms)} for k, atoms in getattr(problem, 'goal_cardinality', ())], 'translation_source': translation_source, 'model_api_calls': 0, 'actual_model_tokens': 0, 'planner_image': image, 'planner_version_disclosure': FD_VERSION_DISCLOSURE, 'search_configuration': search, 'timeout_s': timeout_s, 'memory_mb': memory_mb, 'host_hard_wall_s': hard_wall_s, 'memory_measurement_scope': 'Docker cgroup limit; distinct from Windows Job committed-memory accounting', 'plan_optimality_claimed': False, 'symbolic_cost': 'one unit per physical ground action; nominal runtime costs are measured separately', 'planner_invocations': 0, 'action_ids': [], 'work': {}, 'compiler_internal_robot_actions_added': 0, 'compiler_identity_mapping': 'one PDDL symbol per grounded action; actor and parameters preserved'}
    if measure_resources:
        artifact['resources'] = {'backend': 'docker_cgroup_v2', 'memory_peak_bytes': None, 'memory_peak_source': None, 'memory_limit_bytes': memory_mb * 1024 ** 2, 'limit_reason': None, 'hard_wall_limit_s': hard_wall_s, 'accounting_difference': 'Docker container memory includes kernel-accounted file cache; Windows Job Object reports committed allocation'}

    def finish(status, current_artifact, **kwargs):
        if measure_resources:
            current_artifact['resources']['host_wall_s_including_setup_cleanup'] = time.perf_counter() - hard_started
        return _result(status, current_artifact, **kwargs)
    started = time.perf_counter()
    try:
        compiled = compile_pddl(problem)
    except (ValueError, TypeError) as exc:
        return finish(NATIVE_INPUT_ERROR, artifact, reason=str(exc))
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='fd-', dir=workdir))
    (run_dir / 'domain.pddl').write_text(compiled.domain_text, encoding='utf-8', newline='\n')
    (run_dir / 'problem.pddl').write_text(compiled.problem_text, encoding='utf-8', newline='\n')
    artifact.update(workdir=str(run_dir), domain_pddl_path=str(run_dir / 'domain.pddl'), problem_pddl_path=str(run_dir / 'problem.pddl'), compiler_wall_s=time.perf_counter() - started)
    docker = docker_binary or shutil.which('docker')
    if docker is None:
        return finish(DEPENDENCY_REQUIRED, artifact, reason='Docker executable is unavailable')
    container_name = 'paper4-fd-' + uuid.uuid4().hex
    command = [str(docker), 'run', '--rm', '--pull', 'never', '--network', 'none', '--name', container_name, '--memory', f'{memory_mb}m', '--cpus', '1', '-v', f'{run_dir}:/wd', '-w', '/wd', image, '--overall-time-limit', f'{math.ceil(timeout_s)}s', '--overall-memory-limit', str(memory_mb), 'domain.pddl', 'problem.pddl', '--search', search]
    if measure_resources:
        (run_dir / 'resource_wrapper.py').write_text(_RESOURCE_WRAPPER, encoding='utf-8', newline='\n')
        image_index = command.index(image)
        command[image_index:image_index] = ['--entrypoint', 'python3']
        image_index = command.index(image)
        command[image_index + 1:image_index + 1] = ['/wd/resource_wrapper.py', '/workspace/downward/fast-downward.py']
        artifact['actual_fd_executable'] = '/workspace/downward/fast-downward.py'
    artifact['planner_command'] = command
    artifact['transport_invocations'] = 0
    started = time.perf_counter()
    remaining_wall = timeout_s + 5 if hard_wall_s is None else hard_wall_s - (started - hard_started)
    if remaining_wall <= 0:
        if measure_resources:
            artifact['resources'].update(limit_reason='host_deadline_before_launch', memory_peak_unavailable_reason='no planner/container started')
        return finish(NATIVE_TIMEOUT, artifact, reason='host wall allowance exhausted during compilation/setup')
    try:
        artifact['planner_invocations'] = 1
        artifact['transport_invocations'] = 1
        completed = subprocess.run(command, cwd=run_dir, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=remaining_wall, check=False)
    except subprocess.TimeoutExpired as exc:
        try:
            cleanup = subprocess.run([str(docker), 'rm', '-f', container_name], capture_output=True, text=True, timeout=10, check=False)
            artifact['timeout_cleanup_returncode'] = cleanup.returncode
        except (OSError, subprocess.TimeoutExpired) as cleanup_error:
            artifact['timeout_cleanup_error'] = type(cleanup_error).__name__
        artifact['planner_wall_s'] = time.perf_counter() - started
        if measure_resources:
            artifact['resources']['limit_reason'] = 'host_wall_timeout'
            _read_resources(run_dir, artifact, unavailable_reason='container killed at host deadline before final cgroup sample')
        return finish(NATIVE_TIMEOUT, artifact, reason='host timeout; no plan reused')
    except OSError as exc:
        artifact['planner_invocations'] = 0
        artifact['transport_invocations'] = 0
        artifact['planner_wall_s'] = time.perf_counter() - started
        return finish(DEPENDENCY_REQUIRED, artifact, reason=f'planner launch failed: {exc}')
    artifact.update(planner_wall_s=time.perf_counter() - started, planner_returncode=completed.returncode, planner_reported_solution=completed.returncode in (0, 1, 2, 3), mapper_status='not_attempted', planner_stdout_tail=completed.stdout[-6000:], planner_stderr_tail=completed.stderr[-3000:], work=_work_metrics(completed.stdout), native_log_reports_exhausted_space='Completely explored state space -- no solution!' in completed.stdout, exit_code_interpretation='12 remains incomplete/exhausted; a log phrase alone is not promoted to a native unsolvability certificate')
    if completed.returncode in (125, 126, 127):
        artifact['planner_invocations'] = 0
    if measure_resources:
        if completed.returncode in (20, 22, 24, 137):
            artifact['resources']['limit_reason'] = 'fd_memory_limit_or_container_kill'
        elif completed.returncode in (21, 23):
            artifact['resources']['limit_reason'] = 'fd_time_limit'
        _read_resources(run_dir, artifact)
    if completed.returncode not in (0, 1, 2, 3):
        return finish(classify_fd_failure(completed.returncode), artifact, reason=f'Fast Downward/Docker returned {completed.returncode}')
    plan_path = run_dir / 'sas_plan'
    artifact['planner_plan_path'] = str(plan_path)
    if not plan_path.is_file():
        return finish(NATIVE_INVALID_OUTPUT, artifact, reason='planner reported success without sas_plan')
    try:
        actions = parse_ground_plan(plan_path.read_text(encoding='utf-8'), compiled)
    except (OSError, UnicodeError, PlanParseError) as exc:
        artifact['mapper_status'] = 'invalid_plan_text'
        return finish(NATIVE_INVALID_OUTPUT, artifact, reason=str(exc))
    artifact['mapper_status'] = 'identity_mapped'
    state = frozenset(problem.initial_state)
    for index, action in enumerate(actions):
        if not action.applicable(state):
            artifact['mapper_status'] = 'shared_semantics_precondition_mismatch'
            return finish(NATIVE_INVALID_OUTPUT, artifact, reason=f'compiled plan violates shared preconditions at {index}')
        state = action.apply(state)
    if not problem.goal_met(state):
        artifact['mapper_status'] = 'shared_semantics_goal_mismatch'
        return finish(NATIVE_INVALID_OUTPUT, artifact, reason='compiled plan does not satisfy supplied shared-domain goal')
    plan = [{'primitive': action.primitive, 'agent_id': action.agent_id, 'params': deepcopy(dict(action.params))} for action in actions]
    artifact.update(action_ids=[action.action_id for action in actions], final_state=sorted(state), planner_plan_source='sas_plan', compiled_plan_semantic_replay_valid=True, mapper_status='validated')
    return finish(NATIVE_SOLVED, artifact, plan=plan, zero_step_initial_goal_satisfied=not actions, search_finished_with_resource_limit=completed.returncode in (1, 2, 3))
