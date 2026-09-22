"""Transition."""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from itertools import combinations
from itertools import product
from typing import Any
from typing import FrozenSet
from typing import Mapping
from typing import Tuple
from domains.actions.spec import fact
DOMAIN_VERSION = 'paper4_domain_extension_v1'

@dataclass(frozen=True)
class ConditionalEffect:
    conditions: FrozenSet[str] = frozenset()
    negative_conditions: FrozenSet[str] = frozenset()
    add_effects: FrozenSet[str] = frozenset()
    delete_effects: FrozenSet[str] = frozenset()

    def applicable(self, state):
        return self.conditions <= state and (not self.negative_conditions & state)

@dataclass(frozen=True)
class GroundAction:
    action_id: str
    primitive: str
    agent_id: str
    params: Mapping[str, Any] = field(default_factory=dict)
    preconditions: FrozenSet[str] = frozenset()
    negative_preconditions: FrozenSet[str] = frozenset()
    add_effects: FrozenSet[str] = frozenset()
    delete_effects: FrozenSet[str] = frozenset()
    conditional_effects: Tuple[ConditionalEffect, ...] = ()
    nominal_duration_s: float | None = None
    energy_cost: float | None = None
    source_references: Tuple[str, ...] = ()

    def applicable(self, state):
        return self.preconditions <= state and (not self.negative_preconditions & state)

    def apply(self, state):
        state = frozenset(state)
        if not self.applicable(state):
            raise ValueError(f'action not applicable: {self.action_id}')
        adds, deletes = (set(self.add_effects), set(self.delete_effects))
        for effect in self.conditional_effects:
            if effect.applicable(state):
                adds.update(effect.add_effects)
                deletes.update(effect.delete_effects)
        return frozenset(state - deletes | adds)

    def to_step(self):
        return {'primitive': self.primitive, 'agent_id': self.agent_id, 'params': dict(self.params)}

@dataclass(frozen=True)
class DomainProblem:
    initial_state: FrozenSet[str]
    goal_facts: FrozenSet[str]
    actions: Tuple[GroundAction, ...]
    scenario_id: str
    observation: Mapping[str, Any] = field(default_factory=dict)
    domain_version: str = DOMAIN_VERSION
    negative_goal_facts: FrozenSet[str] = frozenset()
    goal_cardinality: Tuple[Tuple[int, FrozenSet[str]], ...] = ()

    def __post_init__(self):
        for name in ('initial_state', 'goal_facts', 'negative_goal_facts'):
            object.__setattr__(self, name, frozenset(getattr(self, name)))
        object.__setattr__(self, 'actions', tuple(self.actions))
        cardinality = tuple(((k, frozenset(atoms)) for k, atoms in self.goal_cardinality))
        if any((type(k) is not int or k < 0 for k, _ in cardinality)):
            raise ValueError('cardinality threshold must be a nonnegative integer')
        object.__setattr__(self, 'goal_cardinality', cardinality)
        if len({a.action_id for a in self.actions}) != len(self.actions):
            raise ValueError('duplicate ground action identity')

    def goal_met(self, state):
        return self.goal_facts <= state and (not self.negative_goal_facts & state) and all((len(atoms & state) >= threshold for threshold, atoms in self.goal_cardinality))

    def goal_options(self):
        alternatives = [tuple((frozenset(c) for c in combinations(sorted(atoms), threshold))) for threshold, atoms in self.goal_cardinality]
        for choices in product(*alternatives):
            yield frozenset().union(self.goal_facts, *choices)

    def action_by_id(self):
        return {action.action_id: action for action in self.actions}

def with_goal(problem, *, facts=(), cardinality=(), negative=()):
    return replace(problem, goal_facts=frozenset(facts), goal_cardinality=tuple(cardinality), negative_goal_facts=frozenset(negative))

def atom(name, *args):
    return fact(name, *(0.0 if isinstance(v, float) and v == 0 else v for v in args)).render()

def _available(world, aid):
    actor = world.agents[aid]
    return actor.get('available', True) is True and aid not in getattr(world, 'disabled_agents', ()) and (aid not in getattr(world, 'tipped', ()))

def binding_observation(world, scenario_id):
    """Reuse E02's content observation for PSR authorization and grounding.

    PSR's logging snapshot intentionally omits capabilities and communication
    injection fields; it is insufficient as an execution binding on its own.
    No digest or separate receipt is introduced here.
    """
    from copy import deepcopy
    result = deepcopy(world.snapshot_state())
    if scenario_id == 'psr':
        from execution.realization.psr_recovery import _observation
        result['authorization_and_grounding_state'] = _observation(world)
    return result

def authorization_context(world):
    """Fields the fixed primitive sequence does not authorize itself to change.

    Current positions/cargo/tokens evolve through transitions; actor capabilities,
    entity coordinates and injection/policy inputs require fresh grounding if
    changed externally. Runtime energy consumption remains independently checked.
    """
    from copy import deepcopy
    changing = {'position', 'depth_m', 'energy_wh', 'facts', 'cargo', 'docked', 'at_sample'}
    result = {'actors': {aid: {k: v for k, v in actor.items() if k not in changing} for aid, actor in world.agents.items()}}
    for key in ('base_pos', 'relay_pos', 'entry_pos', 'strict_collect', 'injected_unreachable', 'injected_comm_offline', 'disabled_agents', 'tipped', 'peer_conflict', 'comm_outage_until', 'damaged_panel_ids', 'deadlock', 'goal_conflict', 'low_visibility'):
        if hasattr(world, key):
            result[key] = getattr(world, key)
    if hasattr(world, 'samples'):
        result['sample_coordinates'] = {s: v['location'] for s, v in world.samples.items()}
    return deepcopy(result)

def observe(world, scenario_id):
    """Project the actual observation; never accepts a goal or expected outcome."""
    if scenario_id == 'psr':
        from validation.contracts.psr_named import observed_named_facts
        values = {f.render() for f in observed_named_facts(world)}
        values.update((atom('position', aid, *(float(v) for v in actor['position'])) for aid, actor in world.agents.items()))
    elif scenario_id == 'construction':
        values = {f.render() for f in world.global_facts()}
        for aid, actor in world.agents.items():
            values.update((atom('cargo', aid, p) for p in actor['cargo']))
            values.update((atom('reachable_by', aid, p) for p in actor['reachable_entities']))
            if actor['has_arm']:
                values.add(atom('has_arm', aid))
            if hasattr(world, 'local_facts'):
                values.update((atom('at_material', aid, m) for m in actor['at_material']))
                values.update((atom('location_reachable_by', aid, l) for l in actor['reachable_locations']))
                if actor['cargo']:
                    values.add(atom('cargo_nonempty_by', aid))
                capacity = actor['material_capacity']
                if type(capacity) is int and capacity >= 0:
                    values.add(atom('material_cargo_count', aid, len(actor['cargo'])))
                    if len(actor['cargo']) < capacity:
                        values.add(atom('material_cargo_free', aid))
                if actor['has_digging_tool'] is True:
                    values.add(atom('has_digging_tool', aid))
            if aid in world.deadlock or aid in world.goal_conflict:
                values.add(atom('coordination_blocked', aid))
        values.update((atom('damaged', p) for p in world.damaged_panel_ids))
    elif scenario_id == 'lava':
        values = set()
        for sid, sample in world.samples.items():
            values.add(atom('sample_status', sid, sample['status']))
            if sample.get('owner') is not None:
                values.add(atom('owned_by', sid, sample['owner']))
            if sample['status'] == 'stored':
                values.add(atom('in_storage', sid))
        for aid, actor in world.agents.items():
            values.add(atom('position', aid, *(float(v) for v in actor['position'])))
            for target in ('entry', *world.samples):
                if world.at_target(aid, target):
                    values.add(atom('agent_at', aid, target))
            if world.at_entry(aid):
                values.add(atom('at_entry', aid))
            values.update((atom('cargo', aid, sid) for sid in actor['cargo']))
            for key in ('has_collector', 'has_volatiles_sensor'):
                if actor.get(key) is True:
                    values.add(atom(key, aid))
            capacity = actor.get('storage_capacity')
            if type(capacity) is int:
                values.add(atom('cargo_count', aid, len(actor['cargo'])))
            for token in actor['facts']:
                if token in {'headlight_on', 'headlight_off', 'energy_checked'}:
                    values.add(atom(token, aid))
                elif token.startswith('volatiles_reading_published('):
                    values.add(atom('volatiles_reading_published', aid, token[len('volatiles_reading_published('):-1]))
                elif token.startswith('peer_received('):
                    values.add(atom('peer_received', aid, token[len('peer_received('):-1]))
        if world.low_visibility:
            values.add(atom('low_visibility'))
        values.update((atom('unreachable', s) for s in world.injected_unreachable))
        values.update((atom('peer_conflict', a) for a in world.peer_conflict))
        values.update((atom('radio_outage', a) for a, until in world.comm_outage_until.items() if world.sim_time_s < until))
        if world.comm_outage_until:
            import math
            values.add(atom('outage_clock', max(0, math.ceil((max(world.comm_outage_until.values()) - world.sim_time_s) / 5))))
    else:
        raise ValueError(f'unknown domain scenario {scenario_id}')
    for aid in world.agents:
        if _available(world, aid):
            values.add(atom('available', aid))
    return frozenset(values)

def _action(actions, scenario, name, actor, params, *, pre=(), neg=(), add=(), delete=(), conditional=(), duration=None, sources=()):
    actions.append(GroundAction(f'{scenario}_action_{len(actions):04d}', name, actor, params, frozenset(pre) | {atom('available', actor)}, frozenset(neg), frozenset(add), frozenset(delete), tuple(conditional), duration, None, tuple(sources)))

def _psr_actions(world, policy, resource_constraints):
    from domains.actions.psr.tokens import PSR_PRIMITIVES
    from domains.actions.psr.primitives import PSR_PRIMITIVES as RUNTIME_PRIMITIVES
    from validation.contracts.psr_named import authorize_psr_message
    actions = []
    locations = {**{s: tuple(v['location']) for s, v in world.samples.items()}, 'base': tuple(world.base_pos), **{r: tuple(p) for r, p in world.relay_pos.items()}}
    positions = {tuple((float(v) for v in p)) for p in locations.values()} | {tuple((float(v) for v in a['position'])) for a in world.agents.values()}
    for aid, actor in world.agents.items():
        for name, source in PSR_PRIMITIVES.items():
            if actor['agent_type'] not in source.agent_types or name in resource_constraints.get('unavailable_primitives', ()):
                continue
            if 'capabilities' in actor:
                capabilities = actor['capabilities']
                if not isinstance(capabilities, (list, tuple, set, frozenset)) or name not in capabilities:
                    continue
            if name == 'move_to':
                choices = [{'target': s if s in world.samples else list(p)} for s, p in locations.items()]
            elif name in {'sample_collect', 'sample_store'}:
                choices = [{'sample_id': s} for s in world.samples]
            elif name == 'scan_spectral':
                choices = [{'target': s} for s in locations]
            elif name == 'communicate_status':
                choices = [{'target': s} for s in ('base', *world.agents)]
            elif name in {'communicate_relay', 'navigate_to_relay'}:
                choices = [{'relay': r} for r in world.relay_pos]
            elif name == 'sample_offload':
                choices = [{'base': 'base'}]
            else:
                choices = [{}]
            for params in choices:
                try:
                    authorize_psr_message({'primitive': name, 'params': params}, policy)
                except (ValueError, RuntimeError):
                    continue
                pre = {atom(token, aid) for token in source.requires}
                add = {atom(token, aid) for token in source.produces}
                delete = {atom(token, aid) for token in source.clears}
                conditional = []
                if name in {'move_to', 'return_to_base', 'navigate_to_relay'}:
                    position = world.resolve_target(name, params, aid)
                    if position is None:
                        continue
                    delete.update((atom('at_target', aid, target) for target in locations))
                    add.update((atom('at_target', aid, target) for target, p in locations.items() if p == tuple(position)))
                    delete.update((atom('position', aid, *p) for p in positions))
                    add.add(atom('position', aid, *(float(v) for v in position)))
                if name in {'sample_collect', 'sample_store'}:
                    sid = params['sample_id']
                    delete.update((atom('sample_status', sid, value) for value in ('field', 'in_rover', 'stored', 'in_base')))
                    if name == 'sample_collect':
                        add.update({atom('cargo', aid, sid), atom('sample_status', sid, 'in_rover')})
                        delete.add(atom('stored', sid))
                    else:
                        pre.update({atom('cargo', aid, sid), atom('sample_status', sid, 'in_rover')})
                        add.update({atom('stored', sid), atom('sample_status', sid, 'stored')})
                if name == 'sample_offload':
                    for sid in world.samples:
                        for status in ('stored', 'in_rover'):
                            conditional.append(ConditionalEffect(frozenset({atom('cargo', aid, sid), atom('sample_status', sid, status)}), add_effects=frozenset({atom('in_base_storage', sid), atom('sample_status', sid, 'in_base')}), delete_effects=frozenset({atom('cargo', aid, sid), atom('stored', sid), atom('sample_status', sid, 'stored'), atom('sample_status', sid, 'in_rover')})))
                _action(actions, 'psr', name, aid, params, pre=pre, add=add, delete=delete, conditional=conditional, duration=RUNTIME_PRIMITIVES[name].expected_duration_s, sources=('domains/actions/psr/tokens.py#PSR_PRIMITIVES', 'domains/worlds/psr_world.py#apply_structured_effects', 'validation/contracts/psr_named.py#authorize_psr_message'))
    return tuple(actions)

def _construction_actions(world, resource_constraints):
    from domains.actions.construction import CONSTRUCTION_PRIMITIVE_SPECS
    from domains.simulator.lite_simulator import PRIMITIVE_DURATIONS_S
    actions = []
    local_domain = hasattr(world, 'local_facts')
    names = tuple(CONSTRUCTION_PRIMITIVE_SPECS) if local_domain else ('install_bracket', 'panel_align_and_dock')
    locations = sorted(getattr(world, 'locations', ()), key=repr)
    materials = sorted(set(world.panels) | set(world.brackets))
    for aid, actor in world.agents.items():
        for name in names:
            source = CONSTRUCTION_PRIMITIVE_SPECS[name]
            if actor['agent_type'] not in source.allowed_agent_types or name in resource_constraints.get('unavailable_primitives', ()):
                continue
            if 'capabilities' in actor and (not isinstance(actor['capabilities'], (list, tuple, set, frozenset)) or name not in actor['capabilities']):
                continue
            if name == 'cargo_pickup':
                choices = [{'material_id': m} for m in materials]
            elif name == 'cargo_drop':
                choices = [{'target_location': l} for l in locations]
            elif name == 'dig_regolith':
                choices = [{'target_location': l, 'depth': d} for l in locations for d in world.dig_depth_options]
            elif name == 'place_foundation':
                choices = [{'bracket_id': b, 'location': l} for b in world.brackets for l in locations]
            elif name == 'install_bracket':
                choices = [{'bracket_id': b} for b in world.brackets]
            else:
                choices = [{'panel_id': p, 'bracket_id': b} for p in world.panels for b in world.brackets]
            for params in choices:
                pre = {f.render() for f in source.resolve_requires(params)}
                neg = {atom('coordination_blocked', aid)}
                add = {f.render() for f in source.resolve_produces(params)}
                delete, conditional = (set(), [])
                if name == 'install_bracket':
                    pre.remove(atom('agent_has_arm'))
                    pre.add(atom('has_arm', aid))
                elif name == 'panel_align_and_dock':
                    p, b = (params['panel_id'], params['bracket_id'])
                    pre.difference_update({atom('in_cargo', 'ASSEMBLER', p), atom('reachable', p), atom('reachable', b)})
                    pre.update({atom('cargo', aid, p), atom('reachable_by', aid, p), atom('reachable_by', aid, b)})
                    neg.add(atom('damaged', p))
                elif name == 'cargo_pickup':
                    m = params['material_id']
                    pre = {atom('at_material', aid, m), atom('material_cargo_free', aid)}
                    add = {atom('cargo', aid, m), atom('cargo_nonempty_by', aid)}
                    capacity = actor['material_capacity']
                    if type(capacity) is int and capacity > 0:
                        for count in range(min(capacity, len(materials) + 1)):
                            clears = {atom('material_cargo_count', aid, count)}
                            if count + 1 == capacity:
                                clears.add(atom('material_cargo_free', aid))
                            conditional.append(ConditionalEffect(frozenset({atom('material_cargo_count', aid, count)}), frozenset({atom('cargo', aid, m)}), frozenset({atom('material_cargo_count', aid, count + 1)}), frozenset(clears)))
                elif name == 'cargo_drop':
                    pre = {atom('cargo_nonempty_by', aid), atom('location_reachable_by', aid, params['target_location'])}
                elif name == 'dig_regolith':
                    pre.discard(atom('agent_has_digging_tool'))
                    pre.add(atom('has_digging_tool', aid))
                elif name == 'place_foundation':
                    b, l = (params['bracket_id'], params['location'])
                    pre.discard(atom('in_cargo', 'MANIPULATOR', b))
                    pre.add(atom('cargo', aid, b))
                    previous = set(locations)
                    if world.brackets[b]['location'] is not None:
                        previous.add(world.brackets[b]['location'])
                    delete.update((atom('bracket_at_correct_location', b, old) for old in previous))
                    delete.add(atom('bracket_at_correct_location', b))
                    if l == world.brackets[b]['foundation_location']:
                        add.add(atom('bracket_at_correct_location', b))
                _action(actions, 'construction', name, aid, params, pre=pre, neg=neg, add=add, delete=delete, conditional=conditional, duration=PRIMITIVE_DURATIONS_S[name], sources=source.source_references)
    return tuple(actions)

def _lava_actions(world, policy, resource_constraints):
    from domains.worlds.lava_execution_world import SUPPORTED_PRIMITIVES
    from domains.actions.lava.spec import LAVA_SYMBOLIC_PRIMITIVE_SPECS
    from domains.simulator.lite_simulator import PRIMITIVE_DURATIONS_S
    from models.routing import RoutingPolicy
    from models.routing import PipelineStage
    from models.routing import ClientTier
    actions = []
    locations = {'entry': tuple(world.entry_pos), **{s: tuple(v['location']) for s, v in world.samples.items()}}
    positions = tuple(sorted({tuple((float(v) for v in p)) for p in locations.values()} | {tuple((float(v) for v in a['position'])) for a in world.agents.values()}))
    for aid, actor in world.agents.items():
        for name in sorted(SUPPORTED_PRIMITIVES):
            if name == 'peer_communicate':
                try:
                    RoutingPolicy().authorize(policy.get('state', 'Connected'), PipelineStage.EXECUTION_COMMUNICATION, ClientTier.PEER)
                except RuntimeError:
                    continue
            spec = LAVA_SYMBOLIC_PRIMITIVE_SPECS.get(name)
            allowed = spec.allowed_agent_types if spec else {'ROVER', 'SAMPLER'} if name == 'move_to' else {'ROVER'}
            if actor['agent_type'] not in allowed or name in resource_constraints.get('unavailable_primitives', ()):
                continue
            if 'capabilities' in actor:
                capabilities = actor['capabilities']
                if not isinstance(capabilities, (list, tuple, set, frozenset)) or name not in capabilities:
                    continue
            if name in {'move_to', 'low_light_navigation', 'scan_volatiles'}:
                choices = [{'target': t} for t in locations]
            elif name in {'sample_collect', 'sample_store'}:
                choices = [{'sample_id': s} for s in world.samples]
            elif name == 'peer_communicate':
                choices = [{'peer_id': p, 'msg': 'task status'} for p in world.agents if p != aid]
            else:
                choices = [{}]
            for params in choices:
                pre, neg, add, delete, conditional = (set(), set(), set(), set(), [])
                target, sid = (params.get('target'), params.get('sample_id'))
                if target or sid:
                    neg.add(atom('unreachable', target or sid))
                if name in {'move_to', 'low_light_navigation'}:
                    if name == 'low_light_navigation':
                        pre.add(atom('low_visibility'))
                    delete.update((atom('agent_at', aid, t) for t in locations))
                    delete.add(atom('at_entry', aid))
                    import math
                    add.update((atom('agent_at', aid, t) for t, p in locations.items() if math.dist(p, locations[target]) <= 0.5))
                    delete.update((atom('position', aid, *p) for p in positions))
                    add.add(atom('position', aid, *(float(v) for v in locations[target])))
                    if math.dist(locations[target], world.entry_pos) <= 30:
                        add.add(atom('at_entry', aid))
                elif name == 'sample_collect':
                    capacity = actor.get('storage_capacity')
                    if type(capacity) is not int or capacity <= 0:
                        continue
                    pre.update({atom('agent_at', aid, sid), atom('sample_status', sid, 'field'), atom('has_collector', aid)})
                    for count in range(min(capacity, len(world.samples))):
                        _action(actions, 'lava', name, aid, params, pre=pre | {atom('cargo_count', aid, count)}, neg=neg, add={atom('cargo', aid, sid), atom('owned_by', sid, aid), atom('sample_status', sid, 'in_rover'), atom('cargo_count', aid, count + 1)}, delete={atom('sample_status', sid, 'field'), atom('cargo_count', aid, count)} | {atom('owned_by', sid, a) for a in world.agents}, duration=PRIMITIVE_DURATIONS_S[name], sources=spec.source_references)
                    continue
                elif name == 'sample_store':
                    pre.update({atom('cargo', aid, sid), atom('owned_by', sid, aid)})
                    for status in ('in_rover', 'stored'):
                        _action(actions, 'lava', name, aid, params, pre=pre | {atom('sample_status', sid, status)}, neg=neg, add={atom('sample_status', sid, 'stored'), atom('in_storage', sid)}, delete={atom('sample_status', sid, 'in_rover')}, duration=PRIMITIVE_DURATIONS_S[name], sources=spec.source_references)
                    continue
                elif name == 'headlight_toggle':
                    add.add(atom('headlight_on', aid))
                    delete.add(atom('headlight_off', aid))
                elif name == 'energy_check':
                    add.add(atom('energy_checked', aid))
                elif name == 'scan_volatiles':
                    pre.add(atom('has_volatiles_sensor', aid))
                    add.add(atom('volatiles_reading_published', aid, target))
                elif name == 'peer_communicate':
                    import math
                    peer = params['peer_id']
                    for pos in positions:
                        for peerpos in positions:
                            if math.dist(pos, peerpos) <= 30:
                                _action(actions, 'lava', name, aid, params, pre={atom('position', aid, *pos), atom('position', peer, *peerpos)}, neg={atom('peer_conflict', aid), atom('peer_conflict', peer), atom('radio_outage', aid), atom('radio_outage', peer)}, add={atom('peer_received', aid, peer)}, duration=PRIMITIVE_DURATIONS_S[name], sources=spec.source_references)
                    continue
                _action(actions, 'lava', name, aid, params, pre=pre, neg=neg, add=add, delete=delete, duration=PRIMITIVE_DURATIONS_S[name], sources=spec.source_references if spec else ('docs/action_reference.md#shared-action-bindings',))
    if world.comm_outage_until:
        import math
        last = max(world.comm_outage_until.values())
        initial_bucket = max(0, math.ceil((last - world.sim_time_s) / 5))
        timed = []
        for action in actions:
            delta = int(action.nominal_duration_s / 5)
            effects = list(action.conditional_effects)
            for bucket in range(initial_bucket + 1):
                after = max(0, bucket - delta)
                remove = {atom('outage_clock', bucket)}
                simulated_time = world.sim_time_s + (initial_bucket - bucket + delta) * 5
                remove.update((atom('radio_outage', aid) for aid, until in world.comm_outage_until.items() if simulated_time >= until))
                effects.append(ConditionalEffect(frozenset({atom('outage_clock', bucket)}), add_effects=frozenset({atom('outage_clock', after)}), delete_effects=frozenset(remove)))
            timed.append(replace(action, conditional_effects=tuple(effects)))
        actions = timed
    return tuple(actions)

def build_problem(world, scenario_id, *, goal_facts=(), goal_cardinality=(), negative_goal_facts=(), communication_policy=None, resource_constraints=None):
    if scenario_id == 'psr':
        from domains.actions.psr.strict_profile import LEGACY_PSR_PROFILE
        if getattr(world, 'semantics_profile', LEGACY_PSR_PROFILE) != LEGACY_PSR_PROFILE:
            raise ValueError('PSR_PROFILE_MISMATCH: domain v1 requires the legacy PSR world')
    policy = communication_policy or {'state': 'Connected'}
    constraints = resource_constraints or {}
    if scenario_id == 'psr':
        actions = _psr_actions(world, policy, constraints)
    elif scenario_id == 'construction':
        actions = _construction_actions(world, constraints)
    elif scenario_id == 'lava':
        actions = _lava_actions(world, policy, constraints)
    else:
        raise ValueError(f'unknown scenario {scenario_id}')
    return DomainProblem(observe(world, scenario_id), frozenset(goal_facts), actions, scenario_id, {'world': binding_observation(world, scenario_id), 'communication_policy': dict(policy), 'resource_constraints': dict(constraints), 'actor_types': {aid: actor['agent_type'] for aid, actor in world.agents.items()}, 'energy_model_scope': 'formal action model excludes numeric runtime consumption; execution reports actual/unknown costs'}, negative_goal_facts=frozenset(negative_goal_facts), goal_cardinality=tuple(goal_cardinality))

def describe_problem(problem, *, include_actor_types=False):
    """JSON-ready common model/mapper context, generated from the actual domain.

    Each row is one exact supported argument shape, with all arguments supplied.
    Multiple count/position variants can describe the same physical operation;
    callers never need to guess legacy parameter aliases from a second table.
    This finite comparison vocabulary is not a claim to ground arbitrary numeric
    coordinates, messages, or a complete construction site.
    """
    from copy import deepcopy

    def effect_record(effect):
        return {'positive_guards': sorted(effect.conditions), 'negative_guards': sorted(effect.negative_conditions), 'add': sorted(effect.add_effects), 'delete': sorted(effect.delete_effects)}
    result = {'domain_version': problem.domain_version, 'scenario': problem.scenario_id, 'initial_facts': sorted(problem.initial_state), 'goal': {'positive': sorted(problem.goal_facts), 'negative': sorted(problem.negative_goal_facts), 'cardinality': [{'minimum': k, 'entities': sorted(atoms)} for k, atoms in problem.goal_cardinality]}, 'actions': [{'action_id': a.action_id, **deepcopy(a.to_step()), 'required_arguments': sorted(a.params), 'preconditions': sorted(a.preconditions), 'negative_preconditions': sorted(a.negative_preconditions), 'add': sorted(a.add_effects), 'delete': sorted(a.delete_effects), 'conditional_effects': [effect_record(e) for e in a.conditional_effects], 'nominal_duration_s': a.nominal_duration_s, 'numeric_planning_energy_cost': a.energy_cost, 'source_references': list(a.source_references)} for a in problem.actions], 'argument_policy': 'Choose the listed actor and complete params shape; finite grounding only. State prerequisites still apply.', 'cost_scope': problem.observation.get('energy_model_scope'), 'communication_policy': deepcopy(problem.observation.get('communication_policy', {})), 'resource_constraints': deepcopy(problem.observation.get('resource_constraints', {}))}
    if include_actor_types:
        result['actor_types'] = deepcopy(problem.observation['actor_types'])
    return result
