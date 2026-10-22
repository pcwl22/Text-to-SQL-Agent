"""Explicit scripted SDK, no Gold, credentials, network or benchmark answer claims."""
import json
from types import SimpleNamespace as NS


class MockCompletions:
    def __init__(self, scenario):
        self.scenario = scenario
        self.context = None
        self.calls = 0

    def set_context(self, index, snapshot):
        self.context = (index, snapshot)

    def create(self, **params):
        self.calls += 1
        index, snapshot = self.context
        system = params["messages"][0]["content"]
        table = next(iter(snapshot["tables"]))
        column = snapshot["tables"][table]["columns"][0]["name"]
        if system.startswith("Select relevant SQLite"):
            text = json.dumps({"tables": [{"name": table, "columns": [column]}]})
        elif system.startswith("Repair one read-only SQLite"):
            text = "SELECT 1"
        elif self.scenario == "fault_injection_v1" and index % 3:
            if index % 3 == 1:
                absent = "__ablation_absent__"
                while absent in snapshot["tables"]:
                    absent += "_"
                text = f'SELECT * FROM "{absent}"'
            else:
                text = 'SELECT FROM "' + table.replace('"', '""') + '"'
        else:
            text = "SELECT 1"
        return NS(status_code=200, _request_id=f"offline-mock-{self.calls}", model="offline-scripted-mock",
                  choices=[NS(message=NS(content=text), finish_reason="stop")])
