from typing import TypedDict, Annotated, Sequence, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    tenant_id: str
    next_agent: str
    pending_draft: Optional[dict]  # {"entity_type", "supplier_name", "content"} — set by
                                     # negotiation_node, consumed by human_approval_node