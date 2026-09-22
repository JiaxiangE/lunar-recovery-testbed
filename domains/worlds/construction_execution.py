"""E03 source-limited assembly transitions, separate from legacy statistics world.

Only install_bracket and panel_align_and_dock execute. Preconditions/effects and
actor types are read from CONSTRUCTION_PRIMITIVE_SPECS. Cargo/reachability are
actor-local observations; materials and circuit preparation are not simulated.
The source specifies no clears for these primitives: cargo denotes allocated,
delivered material here, not a model of gripper release or transport capacity.
Times reuse the existing hand-set LiteSimulator durations, not measured physics;
no Construction primitive energy model exists, so energy consumption is unknown.

Revision-added, PI-approved projection: the binary placement atom yields the
unary install prerequisite only if its location equals that bracket's configured
foundation. Frozen L1/specs do not supply that rule; they remain untouched.
The default panel/bracket/foundation ID mapping and damage order are fixture
choices fixed before seeing a candidate, not identities specified by L1.
"""
from dataclasses import dataclass
from copy import deepcopy
from typing import Optional
from domains.worlds.construction_world import ConstructionWorld
from domains.actions.construction import CONSTRUCTION_PRIMITIVE_SPECS
from domains.actions.construction import PANEL_IDS
from domains.actions.spec import fact
from domains.actions.spec import resolve_primitive
from domains.simulator.lite_simulator import PRIMITIVE_DURATIONS_S
from domains.injection.construction_injections import apply_effect_construction
from domains.injection.construction_injections import trigger_fires_construction
VERSION = 'construction_assembly_execution_v1'
SUPPORTED_ACTIONS = frozenset({'install_bracket', 'panel_align_and_dock'})
PANEL_ORDER = tuple(sorted(PANEL_IDS, key=lambda p: int(p.split('_')[1])))

@dataclass(frozen=True)
class ConstructionStepResult:
    success: bool
    primitive_name: str
    agent_id: str
    reason_code: str = ''
    detail: str = ''
    elapsed_s: Optional[float] = None
    energy_consumed_wh: Optional[float] = None

class ConstructionExecutionWorld(ConstructionWorld):
    semantics_version = VERSION

    def __init__(self):
        self.panels, self.brackets = ({}, {})
        self.damage_order = PANEL_ORDER
        self.damaged_panel_ids = set()
        self.applied_injections = set()
        super().__init__(n_panels=12)

    @property
    def installed_panels(self):
        return len(self.installed_panel_ids)

    @installed_panels.setter
    def installed_panels(self, value):
        if value != 0:
            raise ValueError('installed count is derived from panel entities, not assignable')
        for panel in self.panels.values():
            if panel['status'] == 'installed':
                panel['status'] = 'available'
                panel['bracket_ids'] = set()

    @property
    def installed_panel_ids(self):
        return {p for p, state in self.panels.items() if state['status'] == 'installed' and p not in self.damaged_panel_ids}

    @property
    def broken_panels(self):
        return len(self.damaged_panel_ids)

    @broken_panels.setter
    def broken_panels(self, count):
        if count == 0:
            self.damaged_panel_ids.clear()
            return
        if count < self.broken_panels or count > len(self.damage_order):
            raise ValueError('invalid damaged panel count')
        for panel in self.damage_order:
            if self.broken_panels >= count:
                break
            if panel not in self.damaged_panel_ids:
                self.damaged_panel_ids.add(panel)
                self.panels[panel]['status'] = 'damaged'

    def reset(self, seed=0):
        super().reset(seed)
        self.panels = {p: {'status': 'available', 'bracket_ids': set()} for p in PANEL_ORDER}
        self.brackets = {f'bracket_{i}': {'foundation_location': f'foundation_{i}', 'location': None, 'secured': False} for i in range(1, 13)}
        self.applied_injections = set()
        for actor in self.agents.values():
            actor['available'] = True
            actor['reachable_entities'] = set()
            actor['has_arm'] = actor['agent_type'] in {'MANIPULATOR', 'ASSEMBLER'}
        return self.snapshot_state()

    def global_facts(self):
        values = {fact('panel_installed', p) for p in self.installed_panel_ids}
        for p in self.installed_panel_ids:
            values.update((fact('panel_docked_to_bracket', p, b) for b in self.panels[p]['bracket_ids']))
        for b, state in self.brackets.items():
            if state['location'] is not None:
                values.add(fact('bracket_at_correct_location', b, state['location']))
                if state['location'] == state['foundation_location']:
                    values.add(fact('bracket_at_correct_location', b))
            if state['secured']:
                values.add(fact('bracket_secured', b))
        if self.circuit_connected:
            values.add(fact('circuit_connected'))
        if self.wasted_material == 0:
            values.add(fact('zero_material_wasted'))
        return frozenset(values)

    def actor_facts(self, actor_id):
        actor = self.agents[actor_id]
        values = set(self.global_facts())
        if 'at_base' in actor['facts']:
            values.add(fact('at_base'))
        values.update((fact('in_cargo', actor['agent_type'], p) for p in actor['cargo']))
        values.update((fact('reachable', entity) for entity in actor['reachable_entities']))
        if actor['has_arm']:
            values.add(fact('agent_has_arm'))
        return frozenset(values)

    def preflight(self, primitive, actor_id, params):

        def fail(code, detail):
            return (ConstructionStepResult(False, primitive, actor_id, code, detail), None)
        if primitive not in SUPPORTED_ACTIONS:
            return fail('UNRESOLVED_SPEC', f'{VERSION} does not execute {primitive}')
        if actor_id not in self.agents:
            return fail('AGENT_CONSTRAINT_FAIL', f'unknown actor {actor_id}')
        actor = self.agents[actor_id]
        if actor_id in self.disabled_agents or not actor.get('available', True):
            return fail('AGENT_CONSTRAINT_FAIL', f'disabled/unavailable actor {actor_id}')
        if actor_id in self.deadlock or actor_id in self.goal_conflict:
            return fail('UNRESOLVED_SPEC', 'coordination-conflict execution is outside the assembly subset')
        if 'capabilities' in actor and primitive not in actor['capabilities']:
            return fail('AGENT_CONSTRAINT_FAIL', f'actor lacks {primitive}')
        resolved = resolve_primitive(CONSTRUCTION_PRIMITIVE_SPECS[primitive], params, actor['agent_type'])
        if not resolved.accepted:
            return fail(resolved.reason_code, resolved.detail)
        bracket = params['bracket_id']
        if bracket not in self.brackets:
            return fail('BAD_ARGUMENTS', f'unknown bracket {bracket}')
        panel = params.get('panel_id')
        if panel is not None and panel not in self.panels:
            return fail('BAD_ARGUMENTS', f'unknown panel {panel}')
        if panel in self.damaged_panel_ids:
            return fail('RESOURCE_CONSTRAINT_FAIL', f'damaged panel {panel}')
        missing = resolved.resolved.requires - self.actor_facts(actor_id)
        if missing:
            return fail('CONTRACT_NOT_ENTAILED', f'unmet preconditions: {sorted((f.render() for f in missing))}')
        return (None, resolved.resolved)

    def _apply(self, resolved):
        for effect in resolved.produces:
            if effect.predicate == 'panel_installed':
                self.panels[effect.args[0]]['status'] = 'installed'
            elif effect.predicate == 'panel_docked_to_bracket':
                self.panels[effect.args[0]]['bracket_ids'].add(effect.args[1])
            elif effect.predicate == 'bracket_secured':
                self.brackets[effect.args[0]]['secured'] = True
            else:
                raise ValueError(f'unsupported source effect {effect}')

    def project_step(self, primitive, actor_id, params):
        """Conditional effect interpretation on a private world, no dispatch/time."""
        failure, resolved = self.preflight(primitive, actor_id, params)
        if failure:
            return failure
        self._apply(resolved)
        return ConstructionStepResult(True, primitive, actor_id)

    def step(self, primitive, actor_id, params=None):
        params = dict(params or {})
        failure, resolved = self.preflight(primitive, actor_id, params)
        if failure:
            return failure
        injected = self.pop_injected_failure(actor_id, primitive)
        if injected:
            return ConstructionStepResult(False, primitive, actor_id, 'INJECTED_FAILURE', injected)
        self._apply(resolved)
        elapsed = PRIMITIVE_DURATIONS_S[primitive]
        self.sim_time_s += elapsed
        self.agent_busy_s[actor_id] = self.agent_busy_s.get(actor_id, 0) + elapsed
        return ConstructionStepResult(True, primitive, actor_id, elapsed_s=elapsed)

    def apply_injection(self, spec):
        if spec.id not in {'inj_const_001', 'inj_const_002', 'inj_const_005'}:
            raise ValueError('unsupported injection in the assembly execution subset')
        if spec.id in self.applied_injections:
            return False
        if not trigger_fires_construction(spec.trigger, self, self.sim_time_s):
            return False
        apply_effect_construction(spec, self)
        self.applied_injections.add(spec.id)
        return True

    def snapshot_state(self):

        def plain(value):
            if isinstance(value, dict):
                return {k: plain(v) for k, v in value.items()}
            if isinstance(value, (set, frozenset)):
                return sorted(value)
            if isinstance(value, tuple):
                return list(value)
            return value
        return plain(deepcopy({'semantics_version': VERSION, 'sim_time_s': self.sim_time_s, 'agents': self.agents, 'agent_busy_s': self.agent_busy_s, 'panels': self.panels, 'brackets': self.brackets, 'installed_panel_ids': self.installed_panel_ids, 'installed_panels': self.installed_panels, 'circuit_connected': self.circuit_connected, 'wasted_material': self.wasted_material, 'damaged_panel_ids': self.damaged_panel_ids, 'damage_order': self.damage_order, 'disabled_agents': self.disabled_agents, 'scene_unsatisfiable': self.scene_unsatisfiable, 'alt_scene_goal': self.alt_scene_goal, 'applied_injections': self.applied_injections, 'current_task_id': self.current_task_id, 'deadlock': self.deadlock, 'goal_conflict': self.goal_conflict, 'injected_comm_offline': self.injected_comm_offline, 'pending_injections': [[a, p, v] for (a, p), v in sorted(self._injected.items())]}))

def assembly_fixture(*, circuit=True, brackets_secured=True, time_s=0):
    """Explicit observed preparation; no installed panels and no preparation execution claim."""
    world = ConstructionExecutionWorld()
    world.reset()
    world.circuit_connected = circuit
    world.sim_time_s = time_s
    world.agents['assembler_1']['cargo'] = set(PANEL_IDS)
    for actor in world.agents.values():
        actor['reachable_entities'] = set(world.panels) | set(world.brackets)
    for bracket in world.brackets.values():
        bracket['location'] = bracket['foundation_location']
        bracket['secured'] = brackets_secured
    return world
