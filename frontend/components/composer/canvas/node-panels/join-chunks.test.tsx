import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import JoinChunksPanel from "./join-chunks";

describe("JoinChunksPanel", () => {
  it("editing the input variable calls onChange with joinChunksVariable, not inputVariable", () => {
    const onChange = vi.fn();
    render(<JoinChunksPanel data={{}} onChange={onChange} currentNodeId="jc-1" />);
    fireEvent.change(screen.getByLabelText(/Input variable/), {
      target: { value: "state.chunks" },
    });
    expect(onChange).toHaveBeenCalledWith({ joinChunksVariable: "state.chunks" });
  });

  it("editing separator/prefix/suffix calls onChange with joinChunks*-prefixed keys", () => {
    const onChange = vi.fn();
    render(<JoinChunksPanel data={{}} onChange={onChange} currentNodeId="jc-1" />);
    fireEvent.change(screen.getByLabelText("Separator"), { target: { value: "---" } });
    expect(onChange).toHaveBeenCalledWith({ joinChunksSeparator: "---" });
    fireEvent.change(screen.getByLabelText("Prefix"), { target: { value: "> " } });
    expect(onChange).toHaveBeenCalledWith({ joinChunksPrefix: "> " });
    fireEvent.change(screen.getByLabelText("Suffix"), { target: { value: " <" } });
    expect(onChange).toHaveBeenCalledWith({ joinChunksSuffix: " <" });
  });

  it("toggling the include-metadata checkbox calls onChange with joinChunksIncludeMetadata", () => {
    const onChange = vi.fn();
    render(<JoinChunksPanel data={{}} onChange={onChange} currentNodeId="jc-1" />);
    fireEvent.click(screen.getByLabelText(/Include metadata/));
    expect(onChange).toHaveBeenCalledWith({ joinChunksIncludeMetadata: true });
  });

  it("migrates legacy inputVariable/separator/prefix/suffix fields to canonical names on load", () => {
    const onChange = vi.fn();
    render(
      <JoinChunksPanel
        data={{
          inputVariable: "state.chunks",
          separator: "---",
          prefix: "> ",
          suffix: " <",
        }}
        onChange={onChange}
        currentNodeId="jc-1"
      />
    );
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        joinChunksVariable: "state.chunks",
        joinChunksSeparator: "---",
        joinChunksPrefix: "> ",
        joinChunksSuffix: " <",
        inputVariable: undefined,
        separator: undefined,
        prefix: undefined,
        suffix: undefined,
      })
    );
  });
});
