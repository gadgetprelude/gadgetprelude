class CalendarAdapterBase:
    provider_name = None

    def build_auth_start_response(self, tenant_key: str, provider_id: int, serializer):
        raise NotImplementedError

    def handle_callback(self, code: str):
        raise NotImplementedError

    def test_connection(self, connection):
        raise NotImplementedError

    def create_event(
        self,
        connection,
        provider,
        service_obj,
        customer_name: str,
        customer_email: str,
        start_at,
        end_at,
    ):
        raise NotImplementedError

    def update_event(
        self,
        connection,
        event_id: str,
        start_at,
        end_at,
    ):
        raise NotImplementedError

    def delete_event(
        self,
        connection,
        event_id: str,
    ):
        raise NotImplementedError
    
    def get_busy_intervals(self, connection, start_at, end_at):
        raise NotImplementedError