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
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRef = useRef(save);
  saveRef.current = save;

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  async function runSave() {
    setStatus("saving");
    try {
      await saveRef.current();
      setStatus("saved");
    } catch (err) {
      setStatus("error");
      throw err;
    }
  }

  function markDirty() {
    setStatus("dirty");
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      // Fire-and-forget: nothing is awaiting this debounced save, so swallow
      // the rethrow here to avoid an unhandled rejection. `saveNow()` below
      // still propagates errors to its caller for explicit manual saves.
      runSave().catch(() => {});
    }, debounceMs);
  }

  async function saveNow() {
    if (timerRef.current) clearTimeout(timerRef.current);
    await runSave();
  }

  return { status, markDirty, saveNow };
}
