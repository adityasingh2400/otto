"""Generate agent-spec.schema.json from the Pydantic source of truth.

    cd packages/spec && uv run python export_schema.py

The Pydantic models in src/lineforge_spec/models.py are authoritative. The JSON
Schema is a generated artifact the dashboard (and any non-Python component) consumes.
"""

import json
import pathlib

from lineforge_spec import AgentSpec

OUT = pathlib.Path(__file__).parent / "agent-spec.schema.json"


def main() -> None:
    schema = AgentSpec.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "LineForge AgentSpec"
    OUT.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {OUT} ({len(schema.get('$defs', {}))} defs)")


if __name__ == "__main__":
    main()
