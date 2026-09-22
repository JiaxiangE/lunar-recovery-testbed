"""Public API behavior without examples or paper-data modules on the import path."""
import ast
from pathlib import Path
import subprocess
import sys
from typing import get_type_hints

from controller.diagnosis import FailureEvent
from controller.problem import RecoveryCase


def test_recovery_case_annotations_resolve():
    assert get_type_hints(RecoveryCase)['failure'] is FailureEvent


def test_declared_python_312_syntax():
    # Syntax compatibility is distinct from executing on that interpreter.
    import controller
    root = Path(controller.__file__).resolve().parents[1]
    for name in ('controller', 'planning', 'models', 'domains', 'validation', 'execution', 'baselines'):
        for source in (root / name).rglob('*.py'):
            ast.parse(source.read_text(encoding='utf-8'), feature_version=(3, 12))


def test_custom_recovery_with_example_and_reproduction_imports_blocked(tmp_path):
    import controller
    root = Path(controller.__file__).resolve().parents[1]
    program = '''
import importlib.abc
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
class BlockStudyImports(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in {'examples','reproduction'}:
            raise AssertionError('runtime imported study module: '+name)
sys.meta_path.insert(0,BlockStudyImports())
from controller.api import recover
from controller.problem import RecoveryCase
from controller.diagnosis import FailureEvent
from domains.worlds.psr_world import PSRWorld
from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
from domains.common import build_problem,atom
from planning.networks import build_tree
from models.literal_actions import task_messages

world=PSRWorld(n_samples=1,semantics_profile=STRICT_PSR_PROFILE)
world.reset(0)
world.agents['rover_1']['position']=(20.,0.,0.)
world.agents['rover_1']['facts'].discard('at_base')
goal=[atom('at_base','rover_1')]
problem=build_problem(world,'psr',goal_facts=goal,communication_policy={'state':'Disconnected'})
chain=[{'primitive':'return_to_base','agent_id':'rover_1','params':{}}]
groups=[[{'id':'return_task','actor':'rover_1','goals':goal,'chain':chain}]]
tree=build_tree(problem,groups)
case=RecoveryCase(cell_id='custom-return',seed=1,world=world,nominal=problem,fallback=None,
    policy_id=None,policy_eligible=False,original_problem=problem,original_tree=tree,
    failed_node_id='return_task.p1',failure=FailureEvent(symptom='C-04_base_unreachable',agent_id='rover_1',node_id='return_task.p1'),
    executed_prefix_ids=(),original_plan=chain,original_information={'original_plan':chain,'tree':tree.model_dump(mode='json')},
    provenance={'source':'custom test input'},communication='Disconnected',scope='T',script_groups={'nominal':groups})
row,calls=recover(case,Path(sys.argv[2]))
assert row['status']=='executed' and row['original_goal_met'] and row['dispatch_n']==1
assert calls['actual_api_calls']==0
assert not any(n.split('.')[0] in {'examples','reproduction'} for n in sys.modules)
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(root), str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
