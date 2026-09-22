import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ReactFlowProvider } from "reactflow";
import { COMPOSER_NODE_TYPES } from "./workflow-canvas";

function renderNode(type: string, data: Record<string, unknown>) {
  const NodeComponent = COMPOSER_NODE_TYPES[type];
  if (!NodeComponent) {
    throw new Error(`No node component registered for type "${type}"`);
  }
  return render(
    <ReactFlowProvider>
      <NodeComponent
        id="d1"
        type={type}
        data={data}
        selected={false}
        isConnectable={true}
        xPos={0}
        yPos={0}
        zIndex={0}
        dragging={false}
      />
    </ReactFlowProvider>
  );
}

describe("BranchingNode — decision choice mode", () => {
  it("renders one handle per configured option, not the static if-else pair", () => {
    const { container } = renderNode("decision", {
      mode: "choice",
      options: [{ label: "billing" }, { label: "technical" }, { label: "sales" }],
    });
    const handles = container.querySelectorAll(".react-flow__handle[data-handleid]");
    // 1 target handle (no data-handleid) + 3 labelled source handles.
    const labelledSourceHandles = Array.from(handles).filter((h) =>
      ["billing", "technical", "sales"].includes(h.getAttribute("data-handleid") ?? "")
    );
    expect(labelledSourceHandles).toHaveLength(3);
  });

  it("renders true/false handles for binary mode", () => {
    const { container } = renderNode("decision", { mode: "binary" });
    const handles = container.querySelectorAll(".react-flow__handle[data-handleid]");
    const ids = Array.from(handles).map((h) => h.getAttribute("data-handleid"));
    expect(ids).toEqual(expect.arrayContaining(["true", "false"]));
  });
});
