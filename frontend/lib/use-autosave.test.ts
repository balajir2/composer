import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useAutosave } from "./use-autosave";

describe("useAutosave", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts idle and moves to dirty on markDirty", () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAutosave(save, 3000));
    expect(result.current.status).toBe("idle");
    act(() => result.current.markDirty());
    expect(result.current.status).toBe("dirty");
  });

  it("debounces rapid markDirty calls into a single save", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAutosave(save, 3000));

    act(() => result.current.markDirty());
    act(() => vi.advanceTimersByTime(1000));
    act(() => result.current.markDirty());
    expect(save).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(save).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("saved");
  });

  it("sets status to error when save rejects", async () => {
    const save = vi.fn().mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useAutosave(save, 1000));

    act(() => result.current.markDirty());
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.status).toBe("error");
  });

  it("saveNow bypasses the debounce and saves immediately", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAutosave(save, 3000));

    act(() => result.current.markDirty());
    await act(async () => {
      await result.current.saveNow();
    });

    expect(save).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("saved");
  });

  it("flushes a pending debounced save on unmount instead of discarding it", () => {
    // Regression: client-side route changes (e.g. Designer -> Settings)
    // don't fire beforeunload, so a dirty edit whose debounce hadn't
    // elapsed yet used to be silently dropped on navigation.
    const save = vi.fn().mockResolvedValue(undefined);
    const { result, unmount } = renderHook(() => useAutosave(save, 3000));

    act(() => result.current.markDirty());
    expect(save).not.toHaveBeenCalled();

    unmount();

    expect(save).toHaveBeenCalledTimes(1);
  });

  it("does not double-save on unmount when there is no pending debounce", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { result, unmount } = renderHook(() => useAutosave(save, 3000));

    act(() => result.current.markDirty());
    await act(async () => {
      await result.current.saveNow();
    });
    expect(save).toHaveBeenCalledTimes(1);

    unmount();

    expect(save).toHaveBeenCalledTimes(1);
  });

  describe("serialized saves", () => {
    function deferred() {
      let resolve!: () => void;
      const promise = new Promise<void>((r) => {
        resolve = r;
      });
      return { promise, resolve };
    }

    it("sends a save requested mid-flight only after the first one resolves", async () => {
      const first = deferred();
      const save = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(undefined);
      const { result } = renderHook(() => useAutosave(save, 3000));

      let second!: Promise<void>;
      act(() => {
        void result.current.saveNow();
      });
      act(() => {
        second = result.current.saveNow();
      });
      expect(save).toHaveBeenCalledTimes(1);

      await act(async () => {
        first.resolve();
        await second;
      });
      expect(save).toHaveBeenCalledTimes(2);
    });

    it("coalesces several requests made while one save is in flight into one", async () => {
      const first = deferred();
      const save = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(undefined);
      const { result } = renderHook(() => useAutosave(save, 3000));

      let waiting: Promise<void>[] = [];
      act(() => {
        void result.current.saveNow();
      });
      act(() => {
        waiting = [result.current.saveNow(), result.current.saveNow(), result.current.saveNow()];
      });
      await act(async () => {
        first.resolve();
        await Promise.all(waiting);
      });
      expect(save).toHaveBeenCalledTimes(2);
    });

    it("holds a debounced save that fires while another is in flight", async () => {
      const first = deferred();
      const save = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(undefined);
      const { result } = renderHook(() => useAutosave(save, 1000));

      act(() => {
        void result.current.saveNow();
      });
      act(() => result.current.markDirty());
      await act(async () => {
        vi.advanceTimersByTime(1000);
      });
      expect(save).toHaveBeenCalledTimes(1);

      await act(async () => {
        first.resolve();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(save).toHaveBeenCalledTimes(2);
    });

    it("flush waits for an in-flight save, then saves again only if still unsaved", async () => {
      const first = deferred();
      const save = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(undefined);
      const { result } = renderHook(() => useAutosave(save, 3000));

      act(() => {
        void result.current.saveNow();
      });
      let flushed = false;
      let flushing!: Promise<void>;
      act(() => {
        flushing = result.current.flush().then(() => {
          flushed = true;
        });
      });
      expect(flushed).toBe(false);
      await act(async () => {
        first.resolve();
        await flushing;
      });
      expect(save).toHaveBeenCalledTimes(1); // nothing was dirtied meanwhile
    });
  });
});
