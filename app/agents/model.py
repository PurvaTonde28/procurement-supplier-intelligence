"""Chat model with automatic Groq -> Gemini fallback, used by every
create_agent() call. This is what actually makes the sub-agents resilient
to Groq's rate limits — the raw "groq:model-name" shorthand has no fallback,
which is what caused the sub-agents to hard-crash once Groq's daily token
quota was exhausted, even though Phase 6's call_llm() router already solved
this problem for direct LLM calls."""
import os
from dotenv import load_dotenv
load_dotenv(override=True)

from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI

_groq_model = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.0,
    api_key=os.getenv("GROQ_API_KEY"),
)

_gemini_model = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.0,
    google_api_key=os.getenv("GOOGLE_API_KEY"),
)

# Used by every create_agent() call in app/agents/graph.py — Groq first,
# automatic fallback to Gemini on any error (rate limit, timeout, etc.)
agent_model = _groq_model.with_fallbacks([_gemini_model])