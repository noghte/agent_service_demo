"""A LangGraph agent hosted by agent-service-toolkit's unmodified FastAPI service."""
import json

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph

from core import get_model, settings
from ticket_contract import AGENT_INSTRUCTIONS, TicketPlan, safe_error


async def plan_ticket(state: MessagesState, config: RunnableConfig):
    try:
        model = get_model(config['configurable'].get('model', settings.DEFAULT_MODEL))
        planner = model.with_structured_output(TicketPlan, method='json_schema', strict=True)
        plan = await planner.ainvoke([
            SystemMessage(content=AGENT_INSTRUCTIONS), state['messages'][-1]], config=config)
        content = plan.model_dump_json()
    except Exception as error:
        # Upstream /invoke hides exception details; carry a redacted failure for the host to reject.
        content = json.dumps({'error_type': type(error).__name__, 'error': safe_error(error)})
    return {'messages': [AIMessage(content=content)]}


ticket_agent = (StateGraph(MessagesState)
                .add_node('plan_ticket', plan_ticket)
                .add_edge(START, 'plan_ticket')
                .add_edge('plan_ticket', END)
                .compile())
