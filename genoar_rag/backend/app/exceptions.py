"""Custom exception classes."""


class SampleNotFoundError(Exception):
    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__(f"Sample not found: {run_id}")


class DatabaseError(Exception):
    pass
