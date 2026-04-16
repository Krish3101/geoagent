from src.agents.schema import GeoTask
from src.tools.registry import registry
from typing import List, Any
import asyncio

class ExecutionManager:
    @staticmethod
    async def execute_plan(task: GeoTask, plan: List[str], run_id: str):
        """
        Executes a sequence of tools based on the plan.
        State is passed along continuously.
        """
        from src.api.websockets import manager
        import json
        
        state = task.model_dump()
        
        try:
            await asyncio.sleep(1)  # Allow frontend WebSocket to connect
            await manager.send_log(run_id, f"Plan initiated: {plan}")
            
            for tool_name in plan:
                try:
                    await manager.send_log(run_id, f"Executing {tool_name}...")
                    tool = registry.get_tool(tool_name)
                    
                    # 2. Extract needed arguments from State for Tool
                    kwargs = {}
                    if tool_name == "geocode":
                        kwargs["location"] = state.get("region_name")
                    elif tool_name == "fetch_raster":
                        kwargs["region_name"] = state.get("region_name")
                        kwargs["start_date"] = state.get("start_date")
                        kwargs["end_date"] = state.get("end_date")
                        kwargs["data_types"] = state.get("data_types", [])
                        kwargs["cloud_cover_lt"] = state.get("cloud_cover_lt", 20)
                        kwargs["max_items"] = state.get("max_items", 1)
                        kwargs["bbox"] = state.get("bbox")
                        kwargs["geometry"] = state.get("geometry")
                        kwargs["run_id"] = run_id
                    
                    # 3. Execute!
                    result = await tool.execute(**kwargs)
                    
                    # 4. Merge results back to state
                    if result:
                        state.update(result)
                        await manager.send_log(run_id, f"{tool_name} success. Keys updated: {list(result.keys())}")
                        
                except Exception as e:
                    await manager.send_log(run_id, f"Tool {tool_name} failed: {e}")
                    raise e
                    
            await manager.send_log(run_id, "Task Completed")
            return state
            
        except Exception as e:
            await manager.send_log(run_id, f"Task Failed: {e}")
            return state
