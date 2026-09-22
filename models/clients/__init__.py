"""§6 — LLM clients (Build Spec §6).

Base LLM (Qwen-Max-class via an OpenAI-compatible gateway, e.g. the n1n key in .env) for
D_S / D_M; the edge LLM (Qwen-7B vLLM) reuses Paper 3's `llm_interface.vllm_client` and is
only needed for D_T Tier-2 + full happy path (workstation GPU). `MockBaseLLMClient` lets all
decomposer logic be unit-tested with no network.
"""
from models.clients.call_ledger import CallLedger
from models.clients.call_ledger import CallLedgerError
from models.clients.call_ledger import LogicalCallRecord
from models.clients.call_ledger import PhysicalAttemptRecord
from models.clients.call_ledger import RunCallBudget
from models.clients.call_ledger import TokenUsage
from models.clients.call_ledger import UNAVAILABLE
from models.clients.call_ledger import endpoint_hostname
from models.clients.call_ledger import prompt_sha256
from models.clients.clients import BaseLLMClient
from models.clients.clients import InstrumentedBaseLLMClient
from models.clients.clients import MockBaseLLMClient
from models.clients.clients import load_dotenv
from models.clients.clients import get_base_config
