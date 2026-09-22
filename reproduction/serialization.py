"""Serialization."""
import json
CELLS = ('B05_psr_strict_disconnected', 'B02_psr_connected_named_store', 'E05_lava_communication_return', 'R_M_psr_collector_authorization_loss', 'R_S_construction_prefix_source_inj005', 'E06_lava_abandonment')

def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
