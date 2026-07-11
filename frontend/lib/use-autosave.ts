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
  const isMountedRef = useRef(true);

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
      // but the write itself still lands.
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
        saveRef.current().catch(() => {});
      }
    };
  }, []);

  async function runSave() {
    setStatus("saving");
    try {
      await saveRef.current();
      if (isMountedRef.current) setStatus("saved");
    } catch (err) {
      if (isMountedRef.current) setStatus("error");
      throw err;
    }
  }

  function markDirty() {
    setStatus("dirty");
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

  return { status, markDirty, saveNow };
}
