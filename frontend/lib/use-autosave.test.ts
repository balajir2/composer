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
});
