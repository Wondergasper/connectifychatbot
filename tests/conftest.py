import os
import pytest
from typing import Dict, Any

# Ensure test environment settings
os.environ["ENVIRONMENT"] = "test"
os.environ["VERIFY_TOKEN"] = "test_verify_token_123"
os.environ["WHATSAPP_APP_SECRET"] = "test_secret_key_456"
os.environ["WHATSAPP_TOKEN"] = ""
os.environ["WHATSAPP_PHONE_ID"] = ""
# Never let tests reach live services configured in .env
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["PAYSTACK_SECRET_KEY"] = ""
os.environ["REDIS_URL"] = ""


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Force the in-memory session store (no network)."""
    async def _none():
        return None

    import app.whatsapp.session as session_mod
    monkeypatch.setattr(session_mod, "get_redis_client", _none)
    session_mod._memory_store.clear()
    yield
    session_mod._memory_store.clear()


class MockSupabaseClient:
    """Mock Supabase client for testing repository without external network calls."""
    def __init__(self):
        self.users = {}
        self.bookings = {}
        self.services = {}

    def from_(self, table_name: str):
        return MockQueryBuilder(self, table_name)

    def rpc(self, rpc_name: str, params: Dict[str, Any]):
        return MockRpcResult(rpc_name, params)


class MockRpcResult:
    def __init__(self, rpc_name: str, params: Dict[str, Any]):
        self.rpc_name = rpc_name
        self.params = params
        self.data = {"status": "success", "rpc": rpc_name, "params": params}
        self.error = None

    def execute(self):
        return self


class MockQueryBuilder:
    def __init__(self, client: MockSupabaseClient, table: str):
        self.client = client
        self.table = table
        self._select_cols = "*"
        self._filters = []
        self._insert_data = None
        self._update_data = None

    def select(self, cols: str = "*"):
        self._select_cols = cols
        return self

    def insert(self, data: Any):
        self._insert_data = data
        return self

    def update(self, data: Any):
        self._update_data = data
        return self

    def eq(self, column: str, value: Any):
        self._filters.append((column, "eq", value))
        return self

    def or_(self, condition: str):
        return self

    def in_(self, column: str, values: list):
        return self

    def order(self, column: str, desc: bool = False, nulls_first: bool = False):
        return self

    def limit(self, count: int):
        return self

    def maybe_single(self):
        return self

    def single(self):
        return self

    def execute(self):
        class Result:
            data = None
            error = None
        res = Result()
        if self._insert_data:
            res.data = {
                "id": "mock-inserted-id-123",
                **(self._insert_data if isinstance(self._insert_data, dict) else {}),
            }
        elif self._update_data:
            res.data = {
                "id": "mock-updated-id-123",
                **(self._update_data if isinstance(self._update_data, dict) else {}),
            }
        else:
            res.data = []
        return res
