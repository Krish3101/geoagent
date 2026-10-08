"""The tools are the contract with the model: no argument may carry a location or a path."""

import re

from app.agent import agent

FORBIDDEN_NAME = re.compile(r"bbox|coord|geom|path|lat|lon|wkt|file", re.IGNORECASE)


def test_tool_schema_has_no_coordinate_or_path_parameters():
    tools = agent._function_toolset.tools
    assert set(tools) == {"resolve_area", "extract_vector", "fetch_ndvi"}

    for tool_name, tool in tools.items():
        for prop_name, prop in tool.tool_def.parameters_json_schema["properties"].items():
            assert not FORBIDDEN_NAME.search(prop_name), f"{tool_name}.{prop_name}"
            # a list of numbers would be a coordinate pair or a bbox
            if prop.get("type") == "array":
                assert prop["items"].get("type") not in ("number", "integer")
