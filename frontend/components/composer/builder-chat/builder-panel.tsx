"use client";

import { useState } from "react";
import { sendBuilderMessage, type BuilderEvent } from "@/lib/api/builder";
import { PlanCard } from "./plan-card";
import { CredentialRequestCard } from "./credential-request-card";
import { PatchDiffCard } from "./patch-diff-card";

type Msg = { role: "user" | "assistant"; text: string; events: BuilderEvent[] };

export function BuilderPanel({
  workflowId,
  onWorkflowUpdated,
}: {
  workflowId: string;
  onWorkflowUpdated: (workflow: { nodes: unknown[]; edges: unknown[] }) => void;
}) {
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [transcript, setTranscript] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(text: string) {
    if (!text.trim()) return;
    setBusy(true);
    setTranscript((t) => [...t, { role: "user", text, events: [] }]);
    setInput("");
    try {
      const res = await sendBuilderMessage(workflowId, { message: text, sessionId });
      if (res.events.some((e) => e.type === "session_expired")) {
        setSessionId(undefined); // next turn opens a fresh session
      } else {
        setSessionId(res.sessionId);
      }
      setTranscript((t) => [...t, { role: "assistant", text: res.reply, events: res.events }]);
      if (res.workflow) onWorkflowUpdated(res.workflow as never);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full w-80 flex-col border-l bg-background">
      <div className="border-b px-3 py-2 text-sm font-semibold">Build with AI</div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {transcript.map((m, i) => (
          <div key={i} className="space-y-1">
            <p className={m.role === "user" ? "text-right text-sm" : "text-sm"}>{m.text}</p>
            {m.events.map((e, j) => (
              <div key={j}>
                {e.type === "plan" && (
                  <PlanCard stages={e.stages} onApprove={() => send("approve the plan")} />
                )}
                {e.type === "credential_request" && (
                  <CredentialRequestCard kind={e.kind} detail={e.detail} />
                )}
                {e.type === "patch_proposal" && (
                  <PatchDiffCard
                    explanation={e.explanation}
                    onApprove={() => send("__APPROVE_PATCH__")}
                    onReject={() => send("__REJECT_PATCH__")}
                  />
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="flex gap-2 border-t p-3">
        <input
          className="min-w-0 flex-1 rounded-md border px-2 py-1 text-sm"
          value={input}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send(input);
          }}
          placeholder="Describe your workflow…"
        />
      </div>
    </div>
  );
}
