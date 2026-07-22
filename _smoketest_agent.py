# -*- coding: utf-8 -*-
"""End-to-end agent run under Claude Code harness auth (subscription login, no API key).

RAG tools (search_documents, topic_timeline, management_guidance) stay removed while the v3
index rebuild is still WRITING (torn-read risk on shared HNSW segments). Skills stay enabled
so we validate discovery + loading + the LLM tool-calling loop on structured data.
"""
import asyncio
import os
import sys

sys.path.insert(0, ".")
from claude_agent_sdk import ClaudeSDKClient  # noqa: E402
from agent import finance_agent as fa  # noqa: E402

RAG_TOOLS = {"search_documents", "topic_timeline", "management_guidance"}


def build_test_options():
    opts = fa.build_options()
    opts.allowed_tools = [t for t in opts.allowed_tools
                          if not any(t.endswith(r) for r in RAG_TOOLS)]
    # Use the harness's OWN claude executable so host-managed OAuth passes through
    # (a generic `claude` on PATH lacks the host-auth-refresh capability).
    execpath = os.environ.get("CLAUDE_CODE_EXECPATH")
    if execpath and os.path.exists(execpath):
        opts.cli_path = execpath
    return opts


async def main():
    q = sys.argv[1] if len(sys.argv) > 1 else (
        "Do a financial-forensics style audit of Balkrishna Industries (BALKRISIND) "
        "using structured data only — skip concall/document search, the index is rebuilding. "
        "Rank findings Critical / Notable / Minor and end with the questions I should dig into.")
    print(">>> QUERY:", q, "\n" + "=" * 74, flush=True)
    async with ClaudeSDKClient(options=build_test_options()) as client:
        await client.query(q)
        async for msg in client.receive_response():
            fa._print_message(msg)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
