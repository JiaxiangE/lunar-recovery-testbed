"""Decomposition."""
from __future__ import annotations
from typing import Annotated
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Union
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator
from domains.predicates import parse_predicate
LAYER_ORDER: List[str] = ['S', 'M', 'T', 'P']

def layer_below(layer: str) -> Optional[str]:
    i = LAYER_ORDER.index(layer)
    return LAYER_ORDER[i + 1] if i + 1 < len(LAYER_ORDER) else None

class Node(BaseModel):
    id: str
    layer: Literal['S', 'M', 'T', 'P']
    goal_nl: str = ''
    precondition: str = ''
    postcondition: str = ''
    context: dict = Field(default_factory=dict)
    status: Literal['pending', 'active', 'complete', 'failed'] = 'pending'
    estimated_duration_s: float = 0.0
    dependencies: List[str] = Field(default_factory=list)
    parent_id: Optional[str] = None
    children_ids: List[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def _check_predicates_parse(self) -> 'Node':
        parse_predicate(self.precondition)
        parse_predicate(self.postcondition)
        return self

class Scene(Node):
    layer: Literal['S'] = 'S'
    formal_postcondition: str = ''

class Mission(Node):
    layer: Literal['M'] = 'M'
    required_agent_types: List[str] = Field(default_factory=list)

class Task(Node):
    layer: Literal['T'] = 'T'
    assigned_agent_id: str
    required_primitives: List[str] = Field(default_factory=list)

class Primitive(Node):
    layer: Literal['P'] = 'P'
    primitive_name: str
    primitive_args: dict = Field(default_factory=dict)
NodeUnion = Annotated[Union[Scene, Mission, Task, Primitive], Field(discriminator='layer')]

class DecompositionTree(BaseModel):
    """A complete tree: nodes keyed by id + a root id, with structural validation."""
    root_id: str
    nodes: Dict[str, NodeUnion]

    def get(self, node_id: str) -> Optional[Node]:
        return self.nodes.get(node_id)

    def children_of(self, node_id: str) -> List[Node]:
        n = self.nodes.get(node_id)
        return [self.nodes[c] for c in (n.children_ids if n else []) if c in self.nodes]

    def nodes_at_layer(self, layer: str) -> List[Node]:
        return [n for n in self.nodes.values() if n.layer == layer]

    @model_validator(mode='after')
    def _validate_structure(self) -> 'DecompositionTree':
        for nid, n in self.nodes.items():
            if n.id != nid:
                raise ValueError(f'node key {nid!r} != node.id {n.id!r}')
        if self.root_id not in self.nodes:
            raise ValueError(f'root_id {self.root_id!r} not in nodes')
        root = self.nodes[self.root_id]
        if root.layer != 'S':
            raise ValueError(f"root must be a Scene (layer 'S'), got {root.layer!r}")
        if root.parent_id is not None:
            raise ValueError('root.parent_id must be None')
        for n in self.nodes.values():
            below = layer_below(n.layer)
            if n.layer == 'P' and n.children_ids:
                raise ValueError(f'Primitive {n.id!r} must be a leaf (no children)')
            for cid in n.children_ids:
                if cid not in self.nodes:
                    raise ValueError(f'node {n.id!r} child {cid!r} missing')
                child = self.nodes[cid]
                if child.parent_id != n.id:
                    raise ValueError(f'child {cid!r}.parent_id={child.parent_id!r} != {n.id!r}')
                if below is None or child.layer != below:
                    raise ValueError(f'layer skip: {n.layer} node {n.id!r} has {child.layer} child {cid!r} (expected {below})')
            for dep in n.dependencies:
                d = self.nodes.get(dep)
                if d is None or d.parent_id != n.parent_id or d.layer != n.layer:
                    raise ValueError(f'node {n.id!r} dependency {dep!r} is not a valid same-layer sibling')
        return self
