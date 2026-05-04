# Execution status truth: backend persistence + frontend completion signal

> **Status (2026-05-04):** Audited and closed for Composer. Highlights:
>
> - **Bug 1 (lost-race persistence) was *not* present** — `LangGraphExecutor._mark_failed()` is `await`ed before the executor returns ([src/engine/langgraph_executor.py](../../../src/engine/langgraph_executor.py)).
> - **Bug 2 (multi-condition completion check) was *not* present** — the React panel dispatches solely on `ev.type === "workflow_completed"` and cross-checks via a DB poll ([frontend/components/composer/canvas/designer-execution-panel.tsx](../../../frontend/components/composer/canvas/designer-execution-panel.tsx)).
> - **Detached-task hardening (recommendation A.2) added** — `_run_with_persistence` in [src/api/run.py](../../../src/api/run.py) catches uncaught crashes/cancellations from the fire-and-forget executor task and stamps `failed` before re-raising.
> - **Stuck-row sweeper (recommendation D) added** — [src/maintenance/execution_sweeper.py](../../../src/maintenance/execution_sweeper.py) runs on a configurable interval (`execution_sweeper_interval_seconds`, default 5 min) and marks rows `failed` once they exceed `execution_stuck_after_seconds` (default 15 min).
> - **Sibling MCP base64 defect: closed.** [src/mcp/sanitize.py](../../../src/mcp/sanitize.py) strips Highspot-style preambles + generic >4KB fenced base64 from every text-channel response in [src/mcp/base.py](../../../src/mcp/base.py) `_render_content_blocks()`.
>
> The historical write-up below stays as the rationale and acceptance criteria reference.

**Context:** Discovered while debugging Highspot agent failures in Open Agent Builder (commit `2888e4d`). The same defect class will exist in Composer if it follows the same pattern of "stream events to client, persist final state to DB at the end." Two bugs collude to make failures invisible.

## Symptom

A user runs a workflow. The UI shows **"completed"**. They look at the output and see only some nodes ran. They reload the page. Same thing. They check the database — the execution row says `status: "running"` indefinitely, with no error message, no `completed_at`, and empty `node_results`. There is no audit trail of what failed, no way to retry, and no way to clean up stuck rows except manually.

## Root cause — two bugs that compound

### Bug 1: Error-path persistence is fire-and-forget

The backend wraps workflow execution in `try / except`. On error, it:

1. Sends a final SSE event to the client.
2. Fires a "mark execution failed" mutation **without awaiting it**.
3. Closes the stream and exits.

When the function lives in a serverless runtime with a hard timeout (Vercel Functions, AWS Lambda, Cloud Run), the runtime kills the process at exit. The fire-and-forget mutation routinely **loses the race against process termination**. Result: the DB row never receives the failure status, no error is stored.

```python
# Wrong — lost-race pattern
except Exception as e:
    await emit_sse('error', {'message': str(e)})
    asyncio.create_task(mark_execution_failed(exec_id, error=str(e)))  # NOT awaited
    return  # process exits, task may not finish
```

### Bug 2: Frontend treats ambiguous signals as completion

The frontend listens to SSE events and updates UI state. The completion check was:

```typescript
if (event === 'workflow_completed'
    || data.status === 'completed'
    || data.status === 'waiting-auth') {
  setStatus('completed')
}
```

The `data.status === 'completed'` shortcut was the trap. **Per-node events** sometimes include a top-level `status` field that means "this node completed," not "the whole workflow completed." Any leak of that field — through a refactor, a new event type, a typo — flips the UI to "completed" while the workflow is still running or has silently failed.

Worse: when the SSE stream just ends (server died, timeout, network drop), the frontend's `while(reader.read())` loop exits via `done`. Without explicit handling, the UI's last-set state persists. If a stale `status: "completed"` event flipped earlier, the user sees "completed" forever.

## Why it matters

These bugs **make the system invisibly broken**:

- Users can't tell when a workflow actually finished vs. died silently.
- Operators can't debug — there's no error in the DB to look at.
- "Stuck running" rows accumulate, polluting dashboards and lists.
- Token spend disappears into runs that produced nothing.
- When you ask "did it work?", neither the UI nor the DB can answer truthfully.

## Detection checklist for Composer

Look for these patterns in Composer's execution code:

1. **Anywhere a "mark complete/failed" DB write happens in an `except`/`finally` block, is it awaited before the function returns?** If the mutation is wrapped in `create_task`, `nursery.start_soon`, `gather(...)` without `await`, or fire-and-forget HTTP — it's at risk.
2. **Does the frontend's "is this run done?" check require a single, explicit terminal event?** If it accepts any of `(event_name, status_field, payload_marker)`, it's at risk. Reduce to one source of truth.
3. **Does the frontend handle stream-close-without-terminal-event?** When `EventSource.close()` or the equivalent fires without ever receiving `workflow_completed`, what state does the UI show? It should show "interrupted" or "unknown," NOT default to "completed."
4. **Is the DB the source of truth, or is the SSE stream?** If the UI reads completion only from SSE, it lies during disconnect. Cross-check against DB status.
5. **Is there a sweeper for stuck `running` rows?** Any row in `running` for >`max_function_duration` should be auto-marked `failed: timeout` by a cron.

## Fix recipe (apply all three)

### A. Always `await` the terminal DB write

```python
except Exception as e:
    logger.exception('workflow execution error')
    if execution_id:
        try:
            await db.executions.complete(
                id=execution_id,
                error=str(e),
            )  # AWAITED — must finish before we exit
        except Exception as persist_err:
            logger.warning(f'failed to persist error: {persist_err}')
    await emit_sse('error', {'error': str(e)})
    await close_stream()
```

The `try` around the persist call is just resilience — it should not silently downgrade to fire-and-forget. The `await` is non-negotiable.

### B. One terminal event in the frontend

```typescript
// Right
if (event === 'workflow_completed') {
  setExecution({ status: data.status || 'completed', ... })
}
```

Drop all the "or this field" shortcuts. The backend emits exactly one terminal event of one of the known types (`workflow_completed`, `workflow_failed`, `workflow_paused`). Frontend dispatches off the event name, period.

### C. Treat stream end without terminal event as "interrupted"

```typescript
const { done } = await reader.read()
if (done) {
  // If we're here without having received a terminal event,
  // the connection died. Don't claim success.
  if (!hasReceivedTerminalEvent) {
    setExecution({ status: 'interrupted', ... })
  }
  break
}
```

Pair this with a separate query that polls the DB row by execution ID. The DB row is the long-lived truth; the SSE stream is just real-time enrichment. UI shows DB status as the headline; SSE drives node-by-node animation.

### D. Stuck-row sweeper

A scheduled job (cron, Convex scheduler, Celery beat — whatever Composer uses):

```python
async def sweep_stuck_executions():
    cutoff = now() - timedelta(seconds=MAX_FUNCTION_DURATION + buffer)
    stuck = await db.executions.find_running_started_before(cutoff)
    for row in stuck:
        await db.executions.complete(
            id=row.id,
            error=f'Function timed out (>{MAX_FUNCTION_DURATION}s) without completing.',
        )
```

Run hourly. Without this, every Vercel/Lambda timeout permanently pollutes your data.

## Acceptance criteria

A test plan that proves the fix:

1. **Kill mid-execution**: start a workflow, kill the worker process before it completes. Within `max_function_duration + sweep_interval`, the DB row is `status: failed, error: "Function timed out..."`. The UI fetches that row and shows "interrupted" or "failed."
2. **Force a runtime exception**: inject a `raise RuntimeError("test")` in a node. The DB row gets `status: failed, error: "test"` reliably across 100 runs (no flakiness). The UI shows the error.
3. **Network drop on client**: disconnect the client mid-stream. The backend run completes normally. The DB row reaches `status: completed`. When the client reconnects, querying the DB shows the run finished — UI updates accordingly.
4. **Stale event leak**: emit a fake `data.status: "completed"` from a non-terminal event. The UI does NOT flip to "completed" — it ignores the field and waits for the explicit terminal event.

## Why this happens architecturally

Stream-based UI feels real-time and elegant — events flow, the UI animates, results trickle in. The temptation is to make the stream the contract. But streams are **lossy by nature**: connections drop, processes die, runtimes time out. A truthful execution status needs a **persistent, transactional source of truth** (the DB row) and a **stream for real-time enrichment** (SSE/WebSocket events). The DB is the constitution; the stream is the news ticker.

Composer should design execution state with this separation explicit from day one. It's much harder to retrofit.

---

## Reference: how Open Agent Builder fixed this

Commit `2888e4d` on 2026-04-30:

- **Backend** ([app/api/workflows/[workflowId]/execute-stream/route.ts](../../app/api/workflows/[workflowId]/execute-stream/route.ts)): wrapped the catch-handler `completeExecution` call in `await` and reordered so the DB write completes before the SSE close + function return.
- **Frontend** ([hooks/useWorkflowExecution.ts](../../hooks/useWorkflowExecution.ts)): collapsed the multi-condition completion check down to `event === 'workflow_completed'` only.
- **Diagnostic helpers** ([convex/admin.ts](../../convex/admin.ts)): added `markStuckExecutionFailed`, `getExecutionRaw`, `inspectLatestExecution`, `recentFailures` for retrospective analysis when (not if) this happens again.

The sweeper (D above) and the DB-as-headline UI pattern (C above) are deferred follow-ups in OAB but should be baseline requirements in Composer.

---

# Sibling defect: MCP response sanitization

**Discovered:** 2026-04-30, immediately after the execution-status fixes above. Same workflow, different failure mode. Worth treating as a sibling defect because both are about *not letting bad data into the agent loop*.

## Symptom

A workflow with an MCP-using agent fails with:

```
400 {"type":"error","error":{"type":"invalid_request_error","message":"prompt is too long: 221156 tokens > 200000 maximum"}}
```

Even after switching the agent to a model with a much larger context window (e.g., 1M-token Opus), the same workflow eventually fails on a different real opportunity. Operators conclude "the agent is too verbose" or "we need to truncate output" — both wrong diagnoses. The actual problem is that the *tool responses* are 5–20× larger than they should be.

## Root cause

Some MCP servers — Highspot's `get_item_content` is the example we hit, but it's a defect class — return the **raw bytes of binary file types (xlsx, pptx, pdf, docx, zip, images) base64-encoded inside the text response channel**. Format Highspot uses:

```
File Content Retrieved
Item ID: 638a7314df63f89e41ceccf9
File Name: file-638a7314df63f89e41ceccf9
Content Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
Format: text
Size: 57928 bytes

Base64 Encoded Content:

```
PK ... (~80KB of base64-encoded zip bytes) ...
```
```

A single 50KB spreadsheet → ~80KB of base64 → ~20K tokens of useless binary in the agent's conversation history. The agent doesn't know what to do with it (the bytes aren't human-readable text or code), can't reason about it, and can't extract the spreadsheet's contents from base64 either. Three such fetches in a single agent run = 60K wasted tokens, and the workflow OOMs at the next reasoning step.

## Why it matters

- Burns context window on garbage. Even on 1M-context models the failure mode is just delayed, not fixed.
- Costs money — every token in conversation history bills on every subsequent tool turn.
- Output quality drops. The agent often "hallucinates" content for the binary item because it can't extract anything from base64 and feels obligated to describe what's in the response.
- Defeats the purpose of search agents — instead of summarizing relevant case studies, the agent waxes about base64 strings.

## Detection checklist

In Composer's MCP integration code, ask:

1. **What does the unwrap layer do with text content from MCP tool responses?** Does it pass it straight through to the agent, or does it sanitize?
2. **Is there anywhere the response could legitimately be `>4KB of contiguous base64-looking text`?** Almost certainly no — that pattern is binary content masquerading as text.
3. **Does any MCP server in your registry have file-fetching tools (`get_item_content`, `get_file`, `download`, etc.)?** If yes, those are the high-risk tools; check what they return on non-text file types.
4. **Does the agent's tool-result pipeline have any size cap?** A hard cap per tool response (e.g., 30KB) is a separate defense-in-depth layer regardless of base64 detection.

## Fix recipe

The strip belongs at the MCP response-unwrap boundary — the single chokepoint every MCP tool response flows through. One fix protects every agent.

```python
def strip_binary_blobs(text: str) -> str:
    """Remove base64-encoded binary content from MCP tool responses
    before they reach the agent's conversation history."""
    if not isinstance(text, str):
        return text

    # Highspot-style format with explicit "Base64 Encoded Content" preamble
    if "Base64 Encoded Content" in text:
        meta = {}
        for line in text.split("\n")[:12]:
            for key in ("Item ID", "File Name", "Content Type", "Size"):
                prefix = f"{key}:"
                if line.startswith(prefix):
                    meta[key] = line[len(prefix):].strip()
        return "\n".join([
            "[Binary file omitted from agent context — content cannot be extracted from binary formats.]",
            f"Item ID: {meta.get('Item ID', 'unknown')}",
            f"File Name: {meta.get('File Name', 'unknown')}",
            f"Content Type: {meta.get('Content Type', 'unknown')}",
            f"Size: {meta.get('Size', 'unknown')}",
            "Use only the search_content metadata (title, summary, URL) for this item. Do not retry get_item_content on it.",
        ])

    # Generic fallback: any contiguous base64-like run >4KB inside a code
    # fence is almost certainly binary content masquerading as text.
    return re.sub(
        r"```[\s\S]*?[A-Za-z0-9+/=\r\n]{4096,}[\s\S]*?```",
        "[Binary blob (>4KB base64) omitted from agent context.]",
        text,
    )


def unwrap_mcp_response(response):
    # ... existing extraction logic ...
    if isinstance(text_content, str):
        text_content = strip_binary_blobs(text_content)
    return text_content
```

The replacement marker matters as much as the strip itself. The agent needs to know:

1. **What the item was** — Content Type, file name, item ID. So it can decide whether to skip or note in output.
2. **That it can't be retrieved** — explicit phrase like "content cannot be extracted from binary formats" stops the agent from retrying the same tool call.
3. **What to do instead** — direct it to use search-result metadata (title, URL, summary) for this item.

Without those three, the agent will sometimes try to fetch the item again, or hallucinate content based on the marker.

## Acceptance criteria

1. **Highspot xlsx item**: invoke `get_item_content` on a Highspot item that's a spreadsheet. The response received by the agent contains the metadata marker, no base64. Total tokens for that tool turn should be <100, not >20K.
2. **Highspot pdf item**: same as above for a PDF item.
3. **Legitimate text item**: invoke `get_item_content` on an actual text-based Highspot item (a markdown doc, a webpage). The full text content passes through unchanged. The strip does not false-positive.
4. **Generic fallback**: simulate an unknown MCP server returning `<text>...```\n<5KB of base64>\n```...</text>`. The fenced base64 block is replaced with the generic marker. Surrounding text remains intact.
5. **Multi-fetch run**: a single agent invocation that calls `get_item_content` 5 times across mixed file types completes without context-window error. Token usage matches expectation (text items contribute their real size; binary items contribute ~50 tokens each).

## Why this happens architecturally

The MCP protocol's text response channel is a **trust boundary**. The server can put anything it wants in there, and the agent's conversation history will accept it verbatim. There's no schema enforcement that says "this is text content the model should reason about" vs. "this is a binary blob that escaped its proper transport." Servers like Highspot's `get_item_content` should ideally:

- Return `application/json` with structured fields (`{"text": "...", "binary_url": "..."}`)
- Or extract text on the server side (xlsx → markdown table, pdf → text)
- Or refuse to return non-text content and signal that out-of-band

But you can't make every MCP server out there well-behaved. **Defensive sanitization on the client side is the only reliable layer.** Build it once in the unwrap path; it covers every server, current and future.

## Reference: how Open Agent Builder fixed this

Commit `a69e02b` on 2026-04-30 (immediately after `2888e4d`):

- **Library** ([lib/workflow/executors/mcp-utils.ts](../../lib/workflow/executors/mcp-utils.ts)): added `stripBinaryBlobs()` helper with the Highspot-specific + generic-fallback logic above. Wired into `unwrapMCPResponse()` so every text MCP response passes through it.
- **No prompt changes were needed.** Earlier band-aid attempts (telling the agent "don't call `get_item_content` on xlsx items") worked unreliably; the server-side strip works deterministically.
