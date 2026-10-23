"""Run the real LangGraph graph/checkpointer with only the provider factory substituted."""
import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from ticket_contract import AGENT_INSTRUCTIONS, MODEL_ALIAS, TicketPlan

ROOT = Path(__file__).resolve().parent


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.planner = SimpleNamespace(ainvoke=AsyncMock(return_value=TicketPlan(
            action='escalate', draft='Review POL-003 and POL-004.', reason='Conflicting closure policies.')))
        self.model = Mock()
        self.model.with_structured_output.return_value = self.planner
        self.factory = Mock(return_value=self.model)
        core = ModuleType('core')
        core.get_model = self.factory
        core.settings = SimpleNamespace(DEFAULT_MODEL=MODEL_ALIAS)
        spec = importlib.util.spec_from_file_location('ticket_agent_under_test', ROOT / 'agents/ticket_agent.py')
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'core': core}):
            spec.loader.exec_module(self.module)
        self.graph = self.module.ticket_agent
        self.graph.checkpointer = InMemorySaver()

    async def invoke(self, thread, text):
        config = {'configurable': {'thread_id': thread, 'user_id': 'demo-user', 'model': MODEL_ALIAS}}
        return await self.graph.ainvoke({'messages': [HumanMessage(content=text)]}, config=config)

    async def test_structured_plan_system_context_and_isolated_checkpoints(self):
        result = await self.invoke('ticket-one', 'Ticket one context')
        self.assertEqual(json.loads(result['messages'][-1].content)['action'], 'escalate')
        self.factory.assert_called_with(MODEL_ALIAS)
        self.model.with_structured_output.assert_called_with(TicketPlan, method='json_schema', strict=True)
        messages = self.planner.ainvoke.call_args.args[0]
        self.assertIsInstance(messages[0], SystemMessage)
        self.assertEqual(messages[0].content, AGENT_INSTRUCTIONS)
        self.assertEqual(messages[1].content, 'Ticket one context')
        await self.invoke('ticket-two', 'Ticket two context')
        state_one = await self.graph.aget_state({'configurable': {'thread_id': 'ticket-one'}})
        self.assertEqual(state_one.values['messages'][0].content, 'Ticket one context')
        self.assertNotIn('Ticket two context', str(state_one.values))
        await self.invoke('ticket-one', 'Updated bounded context')
        self.assertEqual(len(self.planner.ainvoke.call_args.args[0]), 2)
        self.assertEqual(self.planner.ainvoke.call_args.args[0][-1].content, 'Updated bounded context')

    async def test_provider_error_is_returned_as_failure_without_secret(self):
        self.planner.ainvoke.side_effect = RuntimeError('Invalid test-provider-key sk-masked-key')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-provider-key'}):
            result = await self.invoke('failed-ticket', 'Context')
        failure = json.loads(result['messages'][-1].content)
        self.assertEqual(failure['error_type'], 'RuntimeError')
        self.assertNotIn('test-provider-key', failure['error'])
        self.assertNotIn('sk-masked-key', failure['error'])
        self.assertNotIn('action', failure)


if __name__ == '__main__':
    unittest.main()
