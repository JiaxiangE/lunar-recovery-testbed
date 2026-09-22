"""Common."""
from copy import deepcopy
from dataclasses import replace
import math
import domains.transition as legacy
from domains.transition import ConditionalEffect
from domains.transition import DomainProblem
from domains.transition import GroundAction
from domains.transition import atom
from domains.transition import binding_observation
from domains.transition import observe
from domains.transition import describe_problem
from domains.transition import with_goal
from domains.actions.psr.strict_profile import COLLECTION_TOLERANCE_M
from domains.actions.psr.strict_profile import STRICT_NAMED_SEMANTICS
from domains.actions.psr.strict_profile import STRICT_OFFLOAD_STATUSES
from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
from domains.actions.psr.strict_profile import STRICT_TOKEN_SPECS
from domains.actions.psr.strict_profile import require_psr_profile
DOMAIN_VERSION = 'paper4_domain_extension_v2'

def _psr_actions(world, policy, constraints):
    source_actions = legacy._psr_actions(world, policy, constraints)
    targets = {'base': world.base_pos, **world.relay_pos, **{sid: s['location'] for sid, s in world.samples.items()}}
    revised = []
    for action in source_actions:
        pre, neg, add = (set(action.preconditions), set(action.negative_preconditions), set(action.add_effects))
        effects = action.conditional_effects
        if action.primitive == 'sample_collect':
            pre.discard(atom('at_location', action.agent_id))
            pre.update((atom(f, action.agent_id) for f in STRICT_TOKEN_SPECS['sample_collect'].requires))
            pre.add(atom('at_target', action.agent_id, action.params['sample_id']))
            neg.add(atom('unreachable', action.params['sample_id']))
        if action.primitive in {'move_to', 'return_to_base', 'navigate_to_relay'}:
            position = world.resolve_target(action.primitive, dict(action.params), action.agent_id)
            add.difference_update((atom('at_target', action.agent_id, name) for name in targets))
            add.update((atom('at_target', action.agent_id, name) for name, target in targets.items() if math.dist(position, target) <= COLLECTION_TOLERANCE_M))
        if action.primitive == 'sample_offload':
            effects = tuple((effect for effect in effects if any((atom('sample_status', sid, status) in effect.conditions for sid in world.samples for status in STRICT_OFFLOAD_STATUSES))))
        revised.append(replace(action, preconditions=frozenset(pre), negative_preconditions=frozenset(neg), add_effects=frozenset(add), conditional_effects=effects, source_references=action.source_references + ('primitives/psr/strict_profile.py',)))
    return tuple(revised)

def build_problem(world, scenario_id, *, goal_facts=(), goal_cardinality=(), negative_goal_facts=(), communication_policy=None, resource_constraints=None):
    if scenario_id != 'psr':
        return replace(legacy.build_problem(world, scenario_id, goal_facts=goal_facts, goal_cardinality=goal_cardinality, negative_goal_facts=negative_goal_facts, communication_policy=communication_policy, resource_constraints=resource_constraints), domain_version=DOMAIN_VERSION)
    require_psr_profile(world, STRICT_NAMED_SEMANTICS)
    policy = deepcopy(communication_policy or {'state': 'Connected'})
    constraints = deepcopy(resource_constraints or {})
    actions = _psr_actions(world, policy, constraints)
    return DomainProblem(observe(world, 'psr'), frozenset(goal_facts), actions, 'psr', {'world': binding_observation(world, 'psr'), 'communication_policy': policy, 'resource_constraints': constraints, 'actor_types': {aid: a['agent_type'] for aid, a in world.agents.items()}, 'semantics_profile': STRICT_PSR_PROFILE, 'named_semantics_version': STRICT_NAMED_SEMANTICS, 'collection_tolerance_m': COLLECTION_TOLERANCE_M, 'energy_model_scope': 'formal action model excludes numeric runtime consumption; execution reports actual/unknown costs'}, domain_version=DOMAIN_VERSION, negative_goal_facts=frozenset(negative_goal_facts), goal_cardinality=tuple(goal_cardinality))
