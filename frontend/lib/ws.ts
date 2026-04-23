const baseUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

export type ComposerEvent = {
  type:
    | "workflow_started"
    | "node_started"
    | "node_completed"
    | "node_failed"
    | "workflow_completed"
    | "approval_required";
  executionId: string;
  tenantId: string | null;
  timestamp: string;
  [key: string]: unknown;
};

export type ComposerEventHandler = (ev: ComposerEvent) => void;

/**
 * Subscribe to execution events over WebSocket.
 *
 * @param executionId - The execution ID to subscribe to.
 * @param token       - Bearer JWT (caller obtains via useSession().data?.accessToken).
 * @param onEvent     - Callback invoked for each DES-007 event frame.
 * @returns           An unsubscribe function; call it to close the WebSocket.
 */
export function subscribeExecution(
  executionId: string,
  token: string,
  onEvent: ComposerEventHandler
): () => void {
  const wsUrl = baseUrl.replace(/^http/, "ws") + `/executions/${executionId}/ws`;
  const ws = new WebSocket(wsUrl, ["bearer", token]);

  ws.addEventListener("message", (msg) => {
    try {
      const ev = JSON.parse(msg.data as string) as ComposerEvent;
      // Filter out keep-alive frames.
      if ((ev.type as unknown as string) === "__keepalive__") return;
      onEvent(ev);
    } catch {
      /* malformed frame; skip */
    }
  });

  return () => {
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close(1000, "client unsubscribe");
    }
  };
}
