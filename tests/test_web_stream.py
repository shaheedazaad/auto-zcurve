import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from auto_zcurve import web
from auto_zcurve.projects import create_project


class EventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_stream_waits_then_stops_on_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = create_project('Stream', root=root)
            app = web.create_app(token='token', projects_root=root)
            endpoint = next(route.endpoint for route in app.routes if route.path.endswith('/events'))
            request = SimpleNamespace(is_disconnected=AsyncMock(side_effect=[False, True]))
            response = await endpoint(request, project.project_id)
            with patch.object(web.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
                chunks = [chunk async for chunk in response.body_iterator]
            self.assertEqual(chunks, [])
            sleep.assert_awaited_once_with(0.2)
            self.assertEqual(request.is_disconnected.await_count, 2)
            self.assertEqual(response.headers['cache-control'], 'no-store')

    async def test_active_stream_sends_each_event_once_before_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = create_project('Stream', root=root)
            app = web.create_app(token='token', projects_root=root)
            job = web.Job(project.project_id, 'run')
            job.status = 'running'
            job.events.append({'event': 'progress', 'completed': 1, 'total': 2})
            app.state.runtime.jobs[project.project_id] = job
            endpoint = next(route.endpoint for route in app.routes if route.path.endswith('/events'))
            request = SimpleNamespace(is_disconnected=AsyncMock(side_effect=[False, True]))
            response = await endpoint(request, project.project_id)
            with patch.object(web.asyncio, 'sleep', new_callable=AsyncMock):
                chunks = [chunk async for chunk in response.body_iterator]
            self.assertEqual(
                chunks,
                [
                    'event: status\ndata: {"event": "status", "message": "Run queued", "status": "queued"}\n\n',
                    'event: progress\ndata: {"event": "progress", "completed": 1, "total": 2}\n\n',
                ],
            )
