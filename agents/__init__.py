"""A demo-only registry consumed by the upstream toolkit service."""
from langgraph.graph.state import CompiledStateGraph
from schema import AgentInfo

from .ticket_agent import ticket_agent

DEFAULT_AGENT = 'ticket-agent'
AgentGraph = CompiledStateGraph
AgentGraphLike = CompiledStateGraph


def get_agent(agent_id):
    if agent_id != DEFAULT_AGENT:
        raise KeyError(agent_id)
    return ticket_agent


async def load_agent(agent_id):
    get_agent(agent_id)


def get_all_agent_info():
    return [AgentInfo(key=DEFAULT_AGENT, description='Support drafts and policy-conflict escalation notes.')]
