"""Dispatch metering and generation to the declared, observed local backend."""
import models.local_client as local_server_client
import models.vllm_client as vllm_local_client

def is_vllm(service):
    return service.backend.lower().startswith('vllm')

def health(service):
    if not is_vllm(service):
        return local_server_client.health(service)
    result = vllm_local_client.health(service)
    result['health'] = {'status': 'ok'}
    return result

def build_client(service, ledger):
    return (vllm_local_client if is_vllm(service) else local_server_client).build_client(service, ledger)

def sampling_for(service):
    if is_vllm(service):
        return dict(vllm_local_client.SAMPLING)
    return {'temperature': 0.0} if service.role == 'edge' else {'temperature': 0.6, 'top_p': 0.95, 'top_k': 20, 'min_p': 0.0}
