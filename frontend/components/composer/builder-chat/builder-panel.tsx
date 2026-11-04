"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Info, Send, Sparkles } from "lucide-react";
import { sendBuilderMessage, type BuilderEvent } from "@/lib/api/builder";
import { cn } from "@/lib/utils";
import { PlanCard } from "./plan-card";
import { CredentialRequestCard } from "./credential-request-card";
import { PatchDiffCard } from "./patch-diff-card";

type Msg = { role: "user" | "assistant"; text: string; events: BuilderEvent[] };

const SUGGESTIONS = [
  "Search the web for a topic the user gives me and summarize what it finds",
  "When a file is uploaded, extract key details and email me a summary",
  "Answer customer questions from our knowledge base and open a Jira ticket if unsure",
];

// Inline notices for events that are not cards (plan / credential / patch).
function Notice({ event }: { event: BuilderEvent }) {
  if (event.type === "stage_applied") {
    return (
      <div className="flex items-center gap-1.5 rounded-lg bg-emerald-50 px-2.5 py-1.5 text-xs text-emerald-800">
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span>
          Added to canvas · {event.node_count} nodes, {event.edge_count} connections
        </span>
      </div>
    );
  }
  const text =
    event.type === "message"
      ? event.content
      : event.type === "error"
        ? event.detail
        : event.type === "session_expired"
          ? "That session expired, so I started a fresh one. Please resend your last request."
          : null;
  if (text === null) return null;
  return (
    <div className="flex items-start gap-1.5 rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900">
      <Info className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="whitespace-pre-wrap break-words">{text}</span>
    </div>
  );
}

function Bubble({ role, children }: { role: Msg["role"]; children: React.ReactNode }) {
  const user = role === "user";
  return (
    <div className={cn("flex", user ? "justify-end" : "justify-start")}>
      <div
        data-role={role}
        className={cn(
          "max-w-[88%] whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-sm leading-relaxed shadow-sm",
          user
            ? "rounded-br-sm bg-primary text-primary-foreground"
            : "rounded-bl-sm border bg-muted text-foreground"
        )}
      >
        {children}
      </div>
    </div>
  );
}

export function BuilderPanel({
  workflowId,
  onWorkflowUpdated,
  executionId,
}: {
  workflowId: string;
  onWorkflowUpdated: (workflow: { nodes: unknown[]; edges: unknown[] }) => void;
  executionId?: string;
}) {
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [transcript, setTranscript] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // "<message index>:<event index>" of plan / patch cards the user already acted on.
  const [resolved, setResolved] = useState<Set<string>>(new Set());
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [transcript, busy]);

  async function send(text: string, executionId?: string, cardKey?: string) {
    if (!text.trim() || busy) return;
    setBusy(true);
    if (cardKey) setResolved((r) => new Set(r).add(cardKey));
    // Internal sentinel messages (patch approve/reject) are not shown as user text.
    if (!text.startsWith("__")) {
      setTranscript((t) => [...t, { role: "user", text, events: [] }]);
    }
    setInput("");
    try {
      const res = await sendBuilderMessage(workflowId, { message: text, sessionId, executionId });
      if (res.events.some((e) => e.type === "session_expired")) {
        setSessionId(undefined); // next turn opens a fresh session
      } else {
        setSessionId(res.sessionId);
      }
      setTranscript((t) => [...t, { role: "assistant", text: res.reply, events: res.events }]);
      if (res.workflow) onWorkflowUpdated(res.workflow as never);
    } catch {
      setTranscript((t) => [
        ...t,
        {
          role: "assistant",
          text: "Something went wrong reaching the builder. Please try again.",
          events: [],
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full w-96 shrink-0 flex-col border-l bg-background">
      <div className="flex items-center gap-2 border-b px-3 py-2.5">
        <Sparkles className="h-4 w-4 text-primary" aria-hidden />
        <span className="text-sm font-semibold">Build with AI</span>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-3" aria-live="polite">
        {transcript.length === 0 && (
          <div className="space-y-3 pt-2">
            <p className="text-sm text-muted-foreground">
              Describe what you want to automate and I&apos;ll draft the workflow on the canvas. I
              propose a plan first, so nothing changes until you approve it.
            </p>
            <div className="space-y-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  data-testid="builder-suggestion"
                  disabled={busy}
                  onClick={() => send(s)}
                  className="w-full rounded-xl border bg-card px-3 py-2 text-left text-sm hover:bg-muted disabled:opacity-50"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {transcript.map((m, i) => (
          <div key={i} className="space-y-2">
            {m.text && <Bubble role={m.role}>{m.text}</Bubble>}
            {m.events.map((e, j) => {
              const key = `${i}:${j}`;
              return (
                <div key={key}>
                  {e.type === "plan" && (
                    <PlanCard
                      stages={e.stages}
                      approved={resolved.has(key)}
                      disabled={busy}
                      onApprove={() => send("approve the plan", undefined, key)}
                    />
                  )}
                  {e.type === "credential_request" && (
                    <CredentialRequestCard kind={e.kind} detail={e.detail} />
                  )}
                  {e.type === "patch_proposal" && (
                    <PatchDiffCard
                      explanation={e.explanation}
                      decided={resolved.has(key)}
                      disabled={busy}
                      onApprove={() => send("__APPROVE_PATCH__", undefined, key)}
                      onReject={() => send("__REJECT_PATCH__", undefined, key)}
                    />
                  )}
                  <Notice event={e} />
                </div>
              );
            })}
          </div>
        ))}
        {busy && (
          <div className="flex justify-start" role="status" aria-label="Builder is working">
            <div className="flex items-center gap-1 rounded-2xl rounded-bl-sm border bg-muted px-3 py-3">
              {[0, 1, 2].map((d) => (
                <span
                  key={d}
                  className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground"
                  style={{ animationDelay: `${d * 150}ms` }}
                />
              ))}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div className="border-t p-3">
        {executionId && (
          <button
            type="button"
            disabled={busy}
            onClick={() => send("Diagnose this failed run and suggest a fix.", executionId)}
            className="border-destructive/40 bg-destructive/10 hover:bg-destructive/20 mb-2 w-full rounded-md border px-2 py-1 text-xs text-destructive disabled:opacity-50"
          >
            Ask about the failed run
          </button>
        )}
        <div className="flex items-end gap-2">
          <textarea
            rows={2}
            className="focus:ring-primary/40 min-w-0 flex-1 resize-none rounded-xl border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 disabled:opacity-60"
            value={input}
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            placeholder="Describe your workflow…"
          />
          <button
            type="button"
            aria-label="Send"
            disabled={busy || !input.trim()}
            onClick={() => send(input)}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" aria-hidden />
          </button>
        </div>
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          Enter to send · Shift+Enter for a new line
        </p>
      </div>
    </div>
  );
}
