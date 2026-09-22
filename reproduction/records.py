"""Read packaged studies and resolve historical record identifiers at the boundary."""
import gzip
import json
from functools import lru_cache
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT

STUDY_NAMES = {
    'main_v5': 'main_comparison',
    'diagnosis_scope_alignment_v2': 'diagnostic_scope',
    'diagnosis_scope_flat_v2': 'flat_scope_reference',
    'candidate_error_retry_v1': 'candidate_error_inputs',
    'late_candidate_retry_v2': 'retention_inputs',
    'late_candidate_retry_v3': 'child_retry_source',
    'task_capability_pruning_v1': 'capability_screen',
    'feedback_state_clarity_v1': 'child_retry',
    'edge_repair_view_applicability_v1': 'complete_task_view',
    'lava_view_capacity_v1': 'lava_capacity_input',
    'task_view_microstudy_v7': 'task_generation',
    'communication_closure_v1': 'communication_reference',
    'e13_continuous_link_v1': 'continuous_communication',
}

@lru_cache(None)
def source_index():
    return json.loads((PACKAGE_ROOT/'data/source_index.json').read_text(encoding='utf-8'))

def record_path(identifier):
    path=Path(identifier)
    if path.is_absolute():
        path=path.resolve()
        if not path.is_relative_to(PACKAGE_ROOT):raise ValueError('record is outside the installed dataset')
        value=path.relative_to(PACKAGE_ROOT).as_posix()
    else:value=str(identifier).replace('\\','/')
    index=source_index()
    if value in index['records']:value=index['records'][value]
    else:
        for old,new in sorted(index['collections'].items(),key=lambda x:-len(x[0])):
            if value==old or value.startswith(old+'/'):
                value=new+value[len(old):];break
    result=(PACKAGE_ROOT/value).resolve()
    if not result.is_relative_to(PACKAGE_ROOT):raise ValueError('record identifier escapes the dataset')
    return result

def read_record(identifier):
    path=record_path(identifier)
    if path.suffix=='.gz':
        with gzip.open(path,'rt',encoding='utf-8') as stream:return json.load(stream)
    return json.loads(path.read_text(encoding='utf-8'))

def historical_identifier(public):
    """Original identifier for a recorded message's provenance field."""
    value=record_path(public).relative_to(PACKAGE_ROOT).as_posix()
    matches=[old for old,new in source_index()['records'].items() if new==value]
    if len(matches)!=1:raise ValueError('recorded identifier is missing or ambiguous: '+value)
    return str(Path(matches[0]))

def public_locations(value):
    """Canonical locations in a new derived report; raw source files are untouched."""
    if isinstance(value,dict):
        result={k:public_locations(v) for k,v in value.items()}
        if isinstance(result.get('study'),str):
            result['study']=STUDY_NAMES.get(result['study'],result['study'])
        if isinstance(result.get('selected_catalog_studies'),list):
            result['selected_catalog_studies']=[STUDY_NAMES.get(s,s) for s in result['selected_catalog_studies']]
        if isinstance(result.get('by_study'),dict):
            result['by_study']={STUDY_NAMES.get(k,k):v for k,v in result['by_study'].items()}
        return result
    if isinstance(value,list):return [public_locations(v) for v in value]
    if isinstance(value,tuple):return tuple(public_locations(v) for v in value)
    if isinstance(value,str) and value.replace('\\','/') in source_index()['records']:
        return record_path(value).relative_to(PACKAGE_ROOT).as_posix()
    return value
