import { useEffect, useRef, useState } from "react";

export type SaveStatus = "idle" | "dirty" | "saving" | "saved" | "error";

/**
 * Debounced autosave: call `markDirty()` on every change to the tracked
 * data. `save` fires `debounceMs` after the last `markDirty()` call,
 * coalescing rapid edits into a single request. `saveNow()` bypasses the
 * debounce for an explicit manual Save action, sharing the same status
 * so the UI has one source of truth for "is my work saved."
 */
export function useAutosave(save: () => Promise<void>, debounceMs = 3000) {
  const [status, setStatus] = useState<SaveStatus>("idle");
  const statusRef = useRef<SaveStatus>("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRef = useRef(save);
  saveRef.current = save;
  const isMountedRef = useRef(true);
  // At most one save runs at a time, and at most one more waits behind it. Saves
  // carry a version stamp that the previous save's response advances, so a second
  // save sent while the first is in flight would reuse the old stamp and be refused
  // as a conflict with ourselves.
  const runningRef = useRef<Promise<void> | null>(null);
  const queuedRef = useRef<Promise<void> | null>(null);
  const runSaveRef = useRef<() => Promise<void>>(() => Promise.resolve());
  // Bumped by every markDirty. A save that finishes after the counter moved did not
  // include the newer edit, so it must not report "saved".
  const dirtyGenerationRef = useRef(0);

  function updateStatus(next: SaveStatus) {
    statusRef.current = next;
    setStatus(next);
  }

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      // A pending debounce timer means markDirty() fired but the save
      // never went out. Client-side route changes (e.g. Designer ->
      // Settings) don't trigger beforeunload, so without this flush the
      // edit is silently discarded and the next screen shows stale data
      // — exactly the "lost my flow" reports this was added to fix.
      // Fire-and-forget: nothing can display the result after unmount,
      // but the write itself still lands. If the workflow was saved
      // elsewhere meanwhile, the server refuses it with a 409 and the edit
      // is dropped rather than overwriting the other save; there is no page
      // left to ask the user what to do.
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
        runSaveRef.current().catch(() => {});
      }
    };
  }, []);

  function start(): Promise<void> {
    if (isMountedRef.current) updateStatus("saving");
    const sentAt = dirtyGenerationRef.current;
    const p = (async () => {
      try {
        await saveRef.current();
        if (isMountedRef.current) {
          updateStatus(dirtyGenerationRef.current === sentAt ? "saved" : "dirty");
        }
      } catch (err) {
        if (isMountedRef.current) updateStatus("error");
        throw err;
      }
    })();
    runningRef.current = p;
    const clear = () => {
      if (runningRef.current === p) runningRef.current = null;
    };
    p.then(clear, clear);
    return p;
  }

  /** Save now, or right after the save in flight; callers that wait share one queued save. */
  function runSave(): Promise<void> {
    const running = runningRef.current;
    if (!running) return start();
    if (queuedRef.current) return queuedRef.current;
    const queued = running
      .catch(() => {})
      .then(() => {
        queuedRef.current = null;
        return start();
      });
    queuedRef.current = queued;
    return queued;
  }

  runSaveRef.current = runSave;

  function markDirty() {
    dirtyGenerationRef.current += 1;
    updateStatus("dirty");
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      // The timer already fired, so it no longer represents pending work —
      // null it out before the save runs so a concurrent unmount doesn't
      // see a stale non-null ref and flush a redundant duplicate save.
      timerRef.current = null;
      // Fire-and-forget: nothing is awaiting this debounced save, so swallow
      // the rethrow here to avoid an unhandled rejection. `saveNow()` below
      // still propagates errors to its caller for explicit manual saves.
      runSave().catch(() => {});
    }, debounceMs);
  }

  async function saveNow() {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    await runSave();
  }

  /** Resolves once no save is running or queued; failures are not rethrown. */
  async function settled(): Promise<void> {
    while (runningRef.current || queuedRef.current) {
      await (runningRef.current ?? queuedRef.current)?.catch(() => {});
      await Promise.resolve();
    }
  }

  /**
   * Make sure the latest edits are stored before something that reads the stored
   * workflow (the AI builder): wait for a save in flight, then save again if the
   * edits are still unsaved or the last save failed.
   */
  async function flush() {
    await settled();
    if (statusRef.current === "dirty" || statusRef.current === "error" || timerRef.current) {
      await saveNow();
    }
  }

  /** True while an edit is unsaved: dirty, debounce pending, or a save running or queued. */
  function hasPendingWork(): boolean {
    return (
      statusRef.current === "dirty" ||
      statusRef.current === "saving" ||
      timerRef.current !== null ||
      runningRef.current !== null ||
      queuedRef.current !== null
    );
  }

  /**
   * Drop pending work and return to idle, e.g. after the user discards their edits.
   * A save already queued still runs: it sends the freshly loaded graph with the fresh
   * stamp, a harmless redundant PUT.
   */
  function reset() {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    updateStatus("idle");
  }

  return { status, markDirty, saveNow, flush, settled, hasPendingWork, reset };
}
