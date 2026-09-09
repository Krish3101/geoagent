import os
import logging
from typing import Dict, Any, Optional, Set
import asyncio
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL")
        self.redis_client = None
        self.use_redis = False
        
        # Local in-memory fallbacks
        self._local_statuses: Dict[str, str] = {}
        self._local_ws_connections: Set[str] = set()
        self._local_logs: Dict[str, list[str]] = {}
        
        # Local WebSocket connection objects (must be in memory)
        self.active_connections: Dict[str, WebSocket] = {}
        
        if self.redis_url:
            try:
                import redis.asyncio as redis
                self.redis_client = redis.from_url(self.redis_url, decode_responses=True)
                self.use_redis = True
                logger.info(f"Redis configured with URL: {self.redis_url}")
            except Exception as e:
                logger.warning(f"Failed to initialize Redis at {self.redis_url}, falling back to local memory. Error: {e}")
                self.use_redis = False
        else:
            logger.info("REDIS_URL not configured. Using local in-memory state.")

    async def check_connection(self) -> bool:
        if not self.redis_client:
            return False
        try:
            await self.redis_client.ping()
            self.use_redis = True
            return True
        except Exception as e:
            if self.use_redis:
                logger.warning(f"Redis is not reachable, falling back to local state. Error: {e}")
            self.use_redis = False
            return False

    async def get_task_status(self, run_id: str) -> Optional[str]:
        if self.redis_client:
            try:
                await self.check_connection()
            except Exception:
                pass
        
        if self.use_redis:
            try:
                return await self.redis_client.hget("geoagent:task_statuses", run_id)
            except Exception as e:
                logger.warning(f"Redis hget failed: {e}. Falling back to local state.")
                
        return self._local_statuses.get(run_id)

    async def set_task_status(self, run_id: str, status: str):
        if self.redis_client:
            try:
                await self.check_connection()
            except Exception:
                pass
                
        if self.use_redis:
            try:
                await self.redis_client.hset("geoagent:task_statuses", run_id, status)
                await self.redis_client.publish(f"geoagent:status_updates:{run_id}", status)
                return
            except Exception as e:
                logger.warning(f"Redis hset failed: {e}. Falling back to local state.")
                
        self._local_statuses[run_id] = status

    async def get_active_connections(self) -> Set[str]:
        if self.redis_client:
            try:
                await self.check_connection()
            except Exception:
                pass
                
        if self.use_redis:
            try:
                conns = await self.redis_client.smembers("geoagent:active_ws_connections")
                return set(conns)
            except Exception as e:
                logger.warning(f"Redis smembers failed: {e}. Falling back to local state.")
                
        return self._local_ws_connections

    async def register_connection(self, run_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[run_id] = websocket
        
        if self.redis_client:
            try:
                await self.check_connection()
            except Exception:
                pass

        # Replay historical logs to subscriber (FR-5)
        history = []
        if self.use_redis:
            try:
                history = await self.redis_client.lrange(f"geoagent:log_history:{run_id}", 0, -1)
            except Exception as e:
                logger.warning(f"Redis lrange failed: {e}. Falling back to local history.")
                history = self._local_logs.get(run_id, [])
        else:
            history = self._local_logs.get(run_id, [])

        for log_line in history:
            try:
                await websocket.send_text(log_line)
            except Exception as e:
                logger.warning(f"Failed to replay log for {run_id}: {e}")
                break
                
        if self.use_redis:
            try:
                await self.redis_client.sadd("geoagent:active_ws_connections", run_id)
                loop = asyncio.get_running_loop()
                loop.create_task(self._subscribe_to_logs(run_id, websocket))
                return
            except Exception as e:
                logger.warning(f"Redis sadd failed: {e}. Falling back to local state.")
                
        self._local_ws_connections.add(run_id)

    async def unregister_connection(self, run_id: str):
        if run_id in self.active_connections:
            del self.active_connections[run_id]
            
        if self.redis_client:
            try:
                await self.check_connection()
            except Exception:
                pass
                
        if self.use_redis:
            try:
                await self.redis_client.srem("geoagent:active_ws_connections", run_id)
                return
            except Exception as e:
                logger.warning(f"Redis srem failed: {e}. Falling back to local state.")
                
        if run_id in self._local_ws_connections:
            self._local_ws_connections.remove(run_id)

    async def send_log(self, run_id: str, message: str):
        if "Task Completed" in message:
            await self.set_task_status(run_id, "completed")
        elif "Task Failed:" in message:
            await self.set_task_status(run_id, "failed")
        elif "Agent started" in message:
            await self.set_task_status(run_id, "running")

        # Always store in log history buffer for replay
        self._local_logs.setdefault(run_id, []).append(message)
            
        if self.redis_client:
            try:
                await self.check_connection()
            except Exception:
                pass
                
        if self.use_redis:
            try:
                await self.redis_client.rpush(f"geoagent:log_history:{run_id}", message)
                await self.redis_client.publish(f"geoagent:logs:{run_id}", message)
                return
            except Exception as e:
                logger.warning(f"Redis publish/rpush failed: {e}. Falling back to local state.")
                
        if run_id in self.active_connections:
            try:
                await self.active_connections[run_id].send_text(message)
            except Exception as e:
                logger.warning(f"Failed to send log to local WebSocket for {run_id}: {e}")

    async def _subscribe_to_logs(self, run_id: str, websocket: WebSocket):
        try:
            pubsub = self.redis_client.pubsub()
            await pubsub.subscribe(f"geoagent:logs:{run_id}")
            while run_id in self.active_connections:
                try:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message and message["type"] == "message":
                        log_data = message["data"]
                        await websocket.send_text(log_data)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Error in Redis pubsub subscriber loop: {e}")
                    break
                await asyncio.sleep(0.01)
            await pubsub.unsubscribe(f"geoagent:logs:{run_id}")
        except Exception as e:
            logger.error(f"Redis Pub/Sub subscription failed for run {run_id}: {e}")
